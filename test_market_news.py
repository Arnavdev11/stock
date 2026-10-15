"""Offline tests for market_news_updater.py. Fixture responses only: no network, no token, no git.
Run: python3 test_market_news.py"""
import contextlib
import datetime as dt
import io
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import market_news_updater as mn                              # noqa: E402

NOW = dt.datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
HOUR = 3600 * 1000
TOKEN = "SECRET-TOKEN-VALUE-123"
K1, K2, K3 = "NSE_EQ|INE040H01021", "NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"


def item(heading="HDFC Bank posts strong Q2 numbers", hours_ago=3, link="https://upstox.com/news/a/article-1/", summary="Summary text.", **extra):
    d = {"heading": heading, "summary": summary, "thumbnail": "https://assets.example/t.webp", "article_link": link,
         "published_time": NOW_MS - int(hours_ago * HOUR)}
    d.update(extra)
    return {k: v for k, v in d.items() if v is not ...}


def body(groups, pages=1, page=1):
    return {"status": "success", "data": groups, "metadata": {"page": {"page_number": page, "page_size": 100, "total_records": 1, "total_pages": pages}}}


class Fake:
    """Scripted transport. `script` is a list of (status, body) or Exception, consumed in order; the last entry repeats."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout})
        r = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def cfg(**kw):
    c = mn.load_config({})
    c.update(kw)
    return c


def run(c, fake, out, token=TOKEN):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, status = mn.run(c, token, out, fake, lambda s: None, NOW)
    return code, status, buf.getvalue()


def read(out):
    return json.loads((out / "market_news.json").read_text(encoding="utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.out = Path(self.t.name) / "out"
        self.addCleanup(self.t.cleanup)
        p = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network access attempted"))
        p.start()
        self.addCleanup(p.stop)


class Parsing(Base):
    def test_valid_response(self):
        stats = {"seen": 0, "rejected": {}}
        recs, pages = mn.parse_response(body({K1: [item()]}, pages=3), [K1], NOW_MS, stats)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["headline"], r["summary"], r["link"], r["instruments"]), ("HDFC Bank posts strong Q2 numbers", "Summary text.", "https://upstox.com/news/a/article-1/", [K1]))
        self.assertEqual((pages, stats["seen"], stats["rejected"]), (3, 1, {}))
        self.assertNotIn("thumbnail", r)                         # thumbnails are never stored

    def test_data_as_plain_list_is_tolerated_for_a_single_instrument(self):
        stats = {"seen": 0, "rejected": {}}
        recs, _ = mn.parse_response(body([item()]), [K1], NOW_MS, stats)
        self.assertEqual(recs[0]["instruments"], [K1])
        recs, _ = mn.parse_response(body([item()]), [K1, K2], NOW_MS, {"seen": 0, "rejected": {}})
        self.assertEqual(recs[0]["instruments"], [])              # cannot be attributed, still kept

    def test_malformed_bodies_never_raise(self):
        for b in (None, [], "x", 5, {}, {"data": 5}, {"data": {K1: "no"}}, {"data": {"bad key": [item()]}}, {"data": {K1: [1, None, "x", []]}},
                  {"data": {K1: [item()]}, "metadata": 7}, {"data": {K1: [item()]}, "metadata": {"page": {"total_pages": "9"}}}):
            stats = {"seen": 0, "rejected": {}}
            recs, pages = mn.parse_response(b, [K1], NOW_MS, stats)
            self.assertIsInstance(recs, list)
            self.assertIn(pages, (None,))

    def test_missing_fields(self):
        stats = {"seen": 0, "rejected": {}}
        recs, _ = mn.parse_response(body({K1: [
            item(heading=...), item(heading="   "), item(heading="!!!"), item(heading=None), item(heading=123),     # no usable headline
            item(heading="No link or summary", link=..., summary=...),
            item(heading="Bad link", link="javascript:alert(1)"), item(heading="Relative link", link="/news/x"),
            item(heading="Blank summary", summary="  ")]}), [K1], NOW_MS, stats)
        self.assertEqual(stats["rejected"], {"missing_headline": 5})
        got = {r["headline"]: r for r in recs}
        self.assertEqual(sorted(got), ["Blank summary", "Bad link", "No link or summary", "Relative link"] and sorted(["Blank summary", "Bad link", "No link or summary", "Relative link"]))
        self.assertIsNone(got["No link or summary"]["link"])
        self.assertIsNone(got["No link or summary"]["summary"])
        self.assertIsNone(got["Bad link"]["link"])
        self.assertIsNone(got["Relative link"]["link"])
        self.assertIsNone(got["Blank summary"]["summary"])

    def test_text_is_cleaned(self):
        r, _ = mn.normalize_item(item(heading="  <b>Big</b>  news\n\tfor &lt;script&gt;x&lt;/script&gt; Tata  ", summary="a" * 5000), K1, NOW_MS)
        self.assertEqual(r["headline"], "Big news for x Tata")
        self.assertLessEqual(len(r["summary"]), mn.SUMMARY_LIMIT)
        self.assertNotIn("<", r["headline"])


class Timestamps(Base):
    def test_unix_ms_to_utc_and_ist(self):
        ms = 1776251261821                                         # the example in the official documentation
        self.assertEqual(mn.iso_utc(ms), "2026-04-15T11:07:41.821Z")
        self.assertEqual(mn.format_ist(ms), "2026-04-15 16:37 IST")
        r, _ = mn.normalize_item(item(published_time=ms), K1, NOW_MS)
        self.assertEqual((r["published_ms"], r["published_at"]), (ms, "2026-04-15T11:07:41.821Z"))

    def test_digit_string_and_float_are_accepted(self):
        for v in (str(NOW_MS - HOUR), float(NOW_MS - HOUR), NOW_MS - HOUR):
            r, why = mn.normalize_item(item(published_time=v), K1, NOW_MS)
            self.assertIsNone(why)
            self.assertEqual(r["published_ms"], NOW_MS - HOUR)

    def test_invalid_timestamps_rejected(self):
        for v in (None, True, False, "yesterday", "12.5", "", -5, 0, float("nan"), float("inf"), [], {}, 1776251261,   # seconds, not ms
                  int(NOW_MS / 1000)):
            r, why = mn.normalize_item(item(published_time=v), K1, NOW_MS)
            self.assertEqual((r, why), (None, "invalid_timestamp"), repr(v))
        r, why = mn.normalize_item({k: v for k, v in item().items() if k != "published_time"}, K1, NOW_MS)
        self.assertEqual(why, "invalid_timestamp")

    def test_future_timestamps_rejected_with_small_tolerance(self):
        self.assertEqual(mn.normalize_item(item(published_time=NOW_MS + HOUR), K1, NOW_MS)[1], "future_timestamp")
        self.assertIsNone(mn.normalize_item(item(published_time=NOW_MS + 60 * 1000), K1, NOW_MS)[1])


class Dedup(Base):
    def test_same_link_across_instruments_is_one_article(self):
        a = item(heading="Sector rally lifts banks", link="https://upstox.com/news/x/article-9/")
        b = item(heading="Sector rally lifts banks", link="https://upstox.com/news/x/article-9/", summary=...)
        recs = []
        for key, it in ((K1, a), (K2, b)):
            recs.append(mn.normalize_item(it, key, NOW_MS)[0])
        merged, dups = mn.merge([], recs)
        self.assertEqual((len(merged), dups), (1, 1))
        self.assertEqual(merged[0]["instruments"], sorted([K1, K2]))
        self.assertEqual(merged[0]["summary"], "Summary text.")    # filled from the copy that had one

    def test_link_normalisation(self):
        base = mn.link_key("https://Upstox.com/news/a/article-1/")
        for u in ("http://upstox.com/news/a/article-1", "https://upstox.com:443/news/a/article-1/#top",
                  "https://upstox.com/news/a/article-1?utm_source=x&fbclid=1", "https://UPSTOX.com/news/a/article-1/?gclid=z"):
            self.assertEqual(mn.link_key(u), base, u)
        self.assertNotEqual(mn.link_key("https://upstox.com/news/a/article-2/"), base)
        self.assertNotEqual(mn.link_key("https://upstox.com/news/a/article-1?id=2"), base)

    def test_fallback_key_without_link(self):
        a = mn.normalize_item(item(link=..., heading="Reliance wins big order!"), K1, NOW_MS)[0]
        b = mn.normalize_item(item(link=..., heading="reliance wins big order"), K2, NOW_MS)[0]
        c = mn.normalize_item(item(link=..., heading="Reliance wins big order!", hours_ago=9), K1, NOW_MS)[0]
        self.assertEqual(a["id"], b["id"])                         # punctuation / case insensitive
        self.assertNotEqual(a["id"], c["id"])                      # same text at another time is a different article
        self.assertEqual(len(mn.merge([], [a, b, c])[0]), 2)

    def test_link_and_linkless_copies_do_not_collide_wrongly(self):
        a = mn.normalize_item(item(), K1, NOW_MS)[0]
        b = mn.normalize_item(item(link=...), K1, NOW_MS)[0]
        self.assertNotEqual(a["id"], b["id"])


class RetentionAndMerge(Base):
    def test_old_articles_are_dropped_and_new_merged_with_existing(self):
        c = cfg(instruments=[K1])
        run(c, Fake([(200, body({K1: [item("Old existing", hours_ago=20, link="https://u.com/1")]}))]), self.out)
        self.assertEqual([a["headline"] for a in read(self.out)["articles"]], ["Old existing"])
        fake = Fake([(200, body({K1: [item("Fresh one", hours_ago=1, link="https://u.com/2"), item("Too old", hours_ago=60, link="https://u.com/3")]}))])
        code, status, _ = run(c, fake, self.out)
        doc = read(self.out)
        self.assertEqual((code, status), (0, "ok"))
        self.assertEqual([a["headline"] for a in doc["articles"]], ["Fresh one", "Old existing"])   # newest first, "Too old" never kept
        self.assertEqual(doc["run"]["expired_or_out_of_window"], 1)

    def test_existing_records_age_out(self):
        c = cfg(instruments=[K1])
        run(c, Fake([(200, body({K1: [item("Aging", hours_ago=40, link="https://u.com/1")]}))]), self.out)
        later = NOW + dt.timedelta(hours=12)                      # now 52 h old
        with contextlib.redirect_stdout(io.StringIO()):
            mn.run(c, TOKEN, self.out, Fake([(200, body({K1: []}))]), lambda s: None, later)
        self.assertEqual(read(self.out)["articles"], [])

    def test_retention_is_short_until_caching_is_permitted(self):
        self.assertEqual(mn.load_config({})["retention_hours"], 48)
        self.assertEqual(mn.load_config({"MARKET_NEWS_RETENTION_HOURS": "168"})["retention_hours"], 48)
        self.assertEqual(mn.load_config({"MARKET_NEWS_RETENTION_HOURS": "168", "MARKET_NEWS_CACHE_PERMITTED": "yes"})["retention_hours"], 168)
        self.assertEqual(mn.load_config({"MARKET_NEWS_RETENTION_HOURS": "9999", "MARKET_NEWS_CACHE_PERMITTED": "yes"})["retention_hours"], 168)
        self.assertEqual(mn.load_config({"MARKET_NEWS_CACHE_PERMITTED": "true"})["retention_hours"], 48)     # only the exact word "yes"

    def test_seven_day_window_when_permitted(self):
        c = cfg(instruments=[K1], retention_hours=168)
        run(c, Fake([(200, body({K1: [item("Five days", hours_ago=120, link="https://u.com/5"), item("Eight days", hours_ago=192, link="https://u.com/8")]}))]), self.out)
        self.assertEqual([a["headline"] for a in read(self.out)["articles"]], ["Five days"])

    def test_corrupt_and_tampered_existing_output_is_not_trusted(self):
        self.out.mkdir()
        good = mn.normalize_item(item("Keep me", link="https://u.com/k"), K1, NOW_MS)[0]
        evil = dict(good, id="x", headline="<script>alert(1)</script>", link="javascript:alert(1)")
        (self.out / "market_news.json").write_text(json.dumps({"schema": 1, "articles": [good, evil, "junk", {"id": 1}]}), encoding="utf-8")
        run(cfg(instruments=[K1]), Fake([(200, body({K1: []}))]), self.out)
        self.assertEqual([a["headline"] for a in read(self.out)["articles"]], ["Keep me"])
        (self.out / "market_news.json").write_text("{not json", encoding="utf-8")
        code, _, _ = run(cfg(instruments=[K1]), Fake([(200, body({K1: [item()]}))]), self.out)
        self.assertEqual(code, 0)
        self.assertEqual(len(read(self.out)["articles"]), 1)

    def test_output_is_deterministic_and_bounded(self):
        many = [item("Story %d" % i, hours_ago=0.1 + i / 100.0, link="https://u.com/%d" % i) for i in range(30)]
        run(cfg(instruments=[K1]), Fake([(200, body({K1: many}))]), self.out)
        a = (self.out / "market_news.json").read_text(encoding="utf-8")
        run(cfg(instruments=[K1]), Fake([(200, body({K1: many}))]), self.out)
        b = (self.out / "market_news.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(a)["articles"], json.loads(b)["articles"])
        ms = [x["published_ms"] for x in json.loads(a)["articles"]]
        self.assertEqual(ms, sorted(ms, reverse=True))


class Failures(Base):
    def seed(self):
        run(cfg(instruments=[K1, K2], batch_size=1), Fake([(200, body({K1: [item("Seed K1", link="https://u.com/s1")]})),
                                                           (200, body({K2: [item("Seed K2", link="https://u.com/s2")]}))]), self.out)
        return (self.out / "market_news.json").read_bytes()

    def test_partial_failure_keeps_previous_records_and_flags_status(self):
        self.seed()
        fake = Fake([(200, body({K1: [item("New K1", hours_ago=1, link="https://u.com/n1")]})), (500, None)])
        code, status, _ = run(cfg(instruments=[K1, K2], batch_size=1, retries=0), fake, self.out)
        doc = read(self.out)
        self.assertEqual((code, status, doc["status"]), (0, "partial", "partial"))
        self.assertEqual(sorted(a["headline"] for a in doc["articles"]), ["New K1", "Seed K1", "Seed K2"])     # K2's records survive
        self.assertEqual(doc["run"]["batches"], {"planned": 2, "ok": 1, "failed": 1, "skipped": 0})
        self.assertIn("HTTP 500", doc["run"]["errors"])

    def test_total_failure_leaves_previous_output_byte_identical(self):
        before = self.seed()
        mtime = (self.out / "market_news.json").stat().st_mtime_ns
        for script in ([(500, None)], [(502, {"x": 1})], [OSError("boom")], [(200, {"status": "error"})], [(200, "text")], [(400, {"errors": [{"errorCode": "UDAPI1189"}]})], [(404, None)]):
            code, status, out = run(cfg(instruments=[K1, K2], batch_size=1, retries=0), Fake(script), self.out)
            self.assertEqual((code, status), (1, "failed"), script)
            self.assertEqual((self.out / "market_news.json").read_bytes(), before)
            self.assertEqual((self.out / "market_news.json").stat().st_mtime_ns, mtime)
        self.assertEqual([p.name for p in self.out.iterdir()], ["market_news.json"])

    def test_total_failure_creates_no_file_when_none_existed(self):
        code, status, _ = run(cfg(instruments=[K1]), Fake([(500, None)]), self.out)
        self.assertEqual((code, status), (1, "failed"))
        self.assertFalse(self.out.exists())

    def test_error_code_is_kept_but_body_text_is_not(self):
        c, s, text = run(cfg(instruments=[K1]), Fake([(400, {"errors": [{"errorCode": "UDAPI1193", "message": "private details here"}]})]), self.out)
        self.assertIn("HTTP 400 UDAPI1193", text)
        self.assertNotIn("private details", text)

    def test_retry_only_for_timeouts_and_5xx_and_counts_toward_cap(self):
        fake = Fake([OSError("t"), (503, None), (200, body({K1: [item()]}))])
        code, status, _ = run(cfg(instruments=[K1], retries=2, max_requests=3), fake, self.out)
        self.assertEqual((code, len(fake.calls)), (0, 3))
        for st in (400, 404, 429):
            fake = Fake([(st, None)])
            run(cfg(instruments=[K1], retries=2, max_requests=5), fake, self.out / st.__str__())
            self.assertEqual(len(fake.calls), 1, st)
        fake = Fake([(500, None)])
        run(cfg(instruments=[K1], retries=5, max_requests=2), fake, self.out)
        self.assertEqual(len(fake.calls), 2)                       # retries stop at the request cap

    def test_rate_limit_stops_the_run_and_never_retries(self):
        fake = Fake([(200, body({K1: [item()]})), (429, None), (200, body({K3: [item("never reached")]}))])
        code, status, _ = run(cfg(instruments=[K1, K2, K3], batch_size=1, retries=3), fake, self.out)
        doc = read(self.out)
        self.assertEqual((code, status, len(fake.calls)), (0, "partial", 2))
        self.assertTrue(doc["run"]["rate_limited"])
        self.assertEqual(doc["run"]["batches"], {"planned": 3, "ok": 1, "failed": 1, "skipped": 1})
        fake = Fake([(429, None)])
        before = run(cfg(instruments=[K1]), fake, self.out / "x")
        self.assertEqual((before[0], len(fake.calls)), (1, 1))

    def test_pagination_stops_when_a_later_page_fails_and_flags_partial(self):
        fake = Fake([(200, body({K1: [item("Page one")]}, pages=3)), (500, None)])
        code, status, _ = run(cfg(instruments=[K1], max_pages=3, retries=0), fake, self.out)
        self.assertEqual((code, status), (0, "partial"))
        self.assertEqual([a["headline"] for a in read(self.out)["articles"]], ["Page one"])
        self.assertEqual([c["params"]["page_number"] for c in fake.calls], [1, 2])

    def test_a_successful_empty_response_is_ok_and_written(self):
        code, status, _ = run(cfg(instruments=[K1]), Fake([(200, body({}))]), self.out)
        self.assertEqual((code, status, read(self.out)["articles"]), (0, "ok", []))


class Auth(Base):
    def test_rejected_token_aborts_immediately_without_writing_or_leaking(self):
        for st in (401, 403):
            fake = Fake([(st, {"errors": [{"message": "bad token " + TOKEN}]})])
            before = None
            code, status, text = run(cfg(instruments=[K1, K2], batch_size=1), fake, self.out)
            self.assertEqual((code, status, len(fake.calls)), (1, "auth_failed", 1))
            self.assertNotIn(TOKEN, text)
            self.assertFalse(self.out.exists())

    def test_previous_output_survives_an_auth_failure(self):
        run(cfg(instruments=[K1]), Fake([(200, body({K1: [item()]}))]), self.out)
        before = (self.out / "market_news.json").read_bytes()
        run(cfg(instruments=[K1]), Fake([(401, None)]), self.out)
        self.assertEqual((self.out / "market_news.json").read_bytes(), before)

    def test_exceptions_that_echo_the_token_do_not_leak(self):
        err = RuntimeError("failed with header Authorization: Bearer " + TOKEN)
        code, status, text = run(cfg(instruments=[K1, K2], batch_size=1, retries=1), Fake([err]), self.out)
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, text)
        run(cfg(instruments=[K1, K2], batch_size=1), Fake([(200, body({K1: [item()]})), err]), self.out)
        blob = (self.out / "market_news.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("Bearer", blob)
        self.assertIn("RuntimeError", blob)                        # only the exception class name is kept

    def test_token_only_travels_in_the_authorization_header(self):
        fake = Fake([(200, body({K1: [item()]}))])
        run(cfg(instruments=[K1]), fake, self.out)
        call = fake.calls[0]
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, json.dumps(call["params"]) + call["url"])
        self.assertNotIn(TOKEN, (self.out / "market_news.json").read_text(encoding="utf-8"))


class RequestCap(Base):
    def keys(self, n):
        return ["NSE_EQ|INE%06d01" % i + "X" for i in range(n)]

    def test_cap_is_enforced_and_remaining_batches_are_skipped(self):
        fake = Fake([(200, body({}))])
        code, status, _ = run(cfg(instruments=self.keys(100), max_requests=2), fake, self.out)
        doc = read(self.out)
        self.assertEqual((code, status, len(fake.calls)), (0, "partial", 2))
        self.assertEqual(doc["run"]["batches"], {"planned": 4, "ok": 2, "failed": 0, "skipped": 2})
        self.assertEqual((doc["run"]["requests_made"], doc["run"]["request_cap"]), (2, 2))

    def test_config_ceilings(self):
        c = mn.load_config({"MARKET_NEWS_MAX_REQUESTS": "9999", "MARKET_NEWS_BATCH_SIZE": "50", "MARKET_NEWS_PAGE_SIZE": "500", "MARKET_NEWS_MAX_PAGES": "99"})
        self.assertEqual((c["max_requests"], c["batch_size"], c["page_size"], c["max_pages"]), (20, 30, 100, 5))
        c = mn.load_config({"MARKET_NEWS_MAX_REQUESTS": "0", "MARKET_NEWS_BATCH_SIZE": "-3"})
        self.assertEqual((c["max_requests"], c["batch_size"]), (1, 1))
        self.assertEqual(mn.load_config({})["max_requests"], 4)

    def test_no_request_ever_carries_more_than_30_keys_or_a_portfolio_category(self):
        fake = Fake([(200, body({}))])
        run(cfg(instruments=self.keys(95), max_requests=20, batch_size=30), fake, self.out)
        self.assertEqual(len(fake.calls), 4)
        for c in fake.calls:
            self.assertLessEqual(len(c["params"]["instrument_keys"].split(",")), 30)
            self.assertEqual(c["params"]["category"], "instrument_keys")
            self.assertEqual(c["url"], "https://api.upstox.com/v2/news")
            self.assertEqual(set(c["params"]), {"category", "instrument_keys", "page_number", "page_size"})
            self.assertTrue(1 <= c["params"]["page_size"] <= 100 and 1 <= c["params"]["page_number"] <= 100)
        self.assertEqual(mn.CATEGORY, "instrument_keys")

    def test_pacing_between_requests(self):
        sleeps = []
        with contextlib.redirect_stdout(io.StringIO()):
            mn.run(cfg(instruments=self.keys(60), delay=1.5), TOKEN, self.out, Fake([(200, body({}))]), sleeps.append, NOW)
        self.assertEqual(sleeps, [1.5])

    def test_instrument_keys_are_validated(self):
        self.assertEqual(mn.parse_instruments(" NSE_EQ|INE040H01021, NSE_EQ|INE040H01021 ,NSE_EQ|INE002A01018"), [K1, K2])
        for bad in ("RELIANCE", "NSE_EQ|", "NSE_EQ|INE040H01021;DROP", "nse_eq|ine040h01021", "NSE_EQ|INE040H01021&category=holdings"):
            with self.assertRaises(mn.ConfigError):
                mn.parse_instruments(bad)


class FlagAndIsolation(Base):
    def test_disabled_by_default_and_touches_nothing(self):
        class Env(dict):
            def __init__(self, *a):
                super().__init__(*a)
                self.read = []

            def get(self, k, d=None):
                self.read.append(k)
                return super().get(k, d)

        fake = Fake([(200, body({}))])
        for env in ({}, {"MARKET_NEWS_ENABLED": ""}, {"MARKET_NEWS_ENABLED": "no"}, {"MARKET_NEWS_ENABLED": "true"}, {"MARKET_NEWS_ENABLED": "1"}):
            e = Env(env)
            e["UPSTOX_ANALYTICS_TOKEN"] = TOKEN
            e.read.clear()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = mn.main(e, self.out, fake)
            self.assertEqual(code, 0)
            self.assertIn("disabled", buf.getvalue())
            self.assertNotIn("UPSTOX_ANALYTICS_TOKEN", e.read)     # the credential is not even looked at
        self.assertEqual(fake.calls, [])
        self.assertFalse(self.out.exists())

    def test_enabled_needs_a_token_and_valid_config(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mn.main({"MARKET_NEWS_ENABLED": "yes"}, self.out, Fake([(200, body({}))])), 1)
            self.assertEqual(mn.main({"MARKET_NEWS_ENABLED": "yes", "UPSTOX_ANALYTICS_TOKEN": TOKEN, "MARKET_NEWS_INSTRUMENTS": "bogus"}, self.out, Fake([])), 2)
            self.assertEqual(mn.main({"MARKET_NEWS_ENABLED": "yes", "UPSTOX_ANALYTICS_TOKEN": TOKEN, "MARKET_NEWS_MAX_REQUESTS": "many"}, self.out, Fake([])), 2)
        self.assertFalse(self.out.exists())

    def test_enabled_run_through_main(self):
        fake = Fake([(200, body({K1: [item()]}))])
        with contextlib.redirect_stdout(io.StringIO()):
            code = mn.main({"MARKET_NEWS_ENABLED": "YES", "UPSTOX_ANALYTICS_TOKEN": TOKEN, "MARKET_NEWS_INSTRUMENTS": K1, "MARKET_NEWS_DELAY_SECONDS": "0"},
                           self.out, fake, lambda s: None, NOW)
        self.assertEqual((code, len(fake.calls)), (0, 1))
        self.assertEqual(len(read(self.out)["articles"]), 1)

    def test_only_market_news_json_is_written_and_other_files_are_untouched(self):
        self.out.mkdir()
        sentinels = {n: ("keep " + n).encode() for n in ("fundamentals.json", "financials.json", "universe.json", "historical.json", "growth_events.json", "market_dma.json")}
        for n, b in sentinels.items():
            (self.out / n).write_bytes(b)
        sub = self.out.parent / "other"
        sub.mkdir()
        (sub / "stock_directory.json").write_bytes(b"dir")
        run(cfg(instruments=[K1]), Fake([(200, body({K1: [item()]}))]), self.out)
        run(cfg(instruments=[K1]), Fake([(500, None)]), self.out)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(list(sentinels) + ["market_news.json"]))    # no .tmp left behind
        for n, b in sentinels.items():
            self.assertEqual((self.out / n).read_bytes(), b, n)
        self.assertEqual((sub / "stock_directory.json").read_bytes(), b"dir")

    def test_output_states_that_it_is_private_and_unlicensed(self):
        run(cfg(instruments=[K1]), Fake([(200, body({K1: [item()]}))]), self.out)
        doc = read(self.out)
        self.assertIs(doc["public_display"], False)
        self.assertIn("unresolved", doc["licensing"])
        self.assertEqual(doc["retention_hours"], 48)
        self.assertNotIn("thumbnail", json.dumps(doc))
        self.assertNotIn("assets.example", json.dumps(doc))
        json.dumps(doc, allow_nan=False)

    def test_no_switch_can_enable_public_display(self):
        src = (ROOT / "market_news_updater.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"MARKET_NEWS_PUBLIC|PUBLIC_DISPLAY", src))        # no environment switch of that kind exists
        self.assertEqual(len(re.findall(r"public_display", src)), 2)                # the docstring and the hard-coded False in the output
        self.assertEqual(len(re.findall(r'"public_display": False', src)), 1)

    def test_source_level_guards(self):
        src = (ROOT / "market_news_updater.py").read_text(encoding="utf-8")
        code = re.sub(r"(?m)#.*$", "", re.sub(r'""".*?"""', "", src, flags=re.S))
        self.assertNotRegex(code, r"(?m)^import requests|^from requests", )          # network library only imported inside the transport
        self.assertNotIn("upstox_common", code)
        for forbidden in ("subprocess", "os.system", "git ", "workflow", "stocklens-data", "positions", "holdings", "universe.json"):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertEqual(len(re.findall(r"Authorization", code)), 1)             # only the header builder
        self.assertNotRegex(code, r"log\([^)]*(token|headers)")                  # never logs the token or headers

    def test_nothing_else_in_the_repo_changed(self):
        import subprocess
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        changed = {l[3:] for l in r.stdout.splitlines() if "__pycache__" not in l}
        self.assertLessEqual(changed, {"market_news_updater.py", "test_market_news.py"})


if __name__ == "__main__":
    unittest.main()
