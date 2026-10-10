"""Tests for Phase 5K index_data_updater.py (no network, no token). Run: python3 -m unittest test_index_data -v"""
import datetime as dt
import json
import unittest
from pathlib import Path

import index_data_updater as ix

IST = ix.mu.IST
NOW = dt.datetime(2026, 10, 6, 21, 0, tzinfo=IST)         # Tuesday evening
END = dt.date(2026, 10, 6)


def rows_for(*names):
    return [{"instrument_key": "NSE_INDEX|" + n, "name": n, "trading_symbol": n.upper()} for n in names]


def candles(frm, to, base=1000.0, step=1.0):
    out, d, i = [], frm, 0
    while d <= to:
        if d.weekday() < 5:
            c = round(base + step * i, 2)
            out.append({"date": d.isoformat(), "open": c, "high": c + 5, "low": c - 5, "close": c, "volume": 0})
            i += 1
        d += dt.timedelta(days=1)
    return out


class FakeClient:
    def __init__(self, data, fail_for=None):
        self.data, self.fail_for, self.calls = data, fail_for, []

    def candles(self, key, frm, to):
        self.calls.append((key, frm, to))
        if key == self.fail_for:
            return None, "HTTP 500"
        return [c for c in self.data.get(key, []) if frm <= c["date"] <= to], None


class Resolve(unittest.TestCase):
    def test_exact_match_only(self):
        res, un = ix.resolve(rows_for("Nifty 50", "Nifty Bank", "Nifty Next 50"))
        labels = {r["label"]: r for r in res}
        self.assertEqual(labels["NIFTY 50"]["instrument_key"], "NSE_INDEX|Nifty 50")
        self.assertEqual(labels["BANK NIFTY"]["instrument_key"], "NSE_INDEX|Nifty Bank")       # alias for the official name
        self.assertEqual(labels["NIFTY NEXT 50"]["instrument_key"], "NSE_INDEX|Nifty Next 50")

    def test_similar_names_are_not_accepted(self):
        res, un = ix.resolve(rows_for("Nifty 500", "Nifty 50 Equal Weight", "Nifty Midcap 150"))
        self.assertNotIn("NIFTY 50", [r["label"] for r in res])
        self.assertNotIn("NIFTY MIDCAP 100", [r["label"] for r in res])
        u = {x["label"]: x for x in un}
        self.assertIn("no instrument", u["NIFTY 50"]["reason"])
        self.assertIn("Nifty Midcap 150", u["NIFTY MIDCAP 100"]["nearest_names"])

    def test_nothing_is_fabricated(self):
        res, un = ix.resolve([])
        self.assertEqual(res, [])
        self.assertEqual(len(un), len(ix.WANTED))
        self.assertTrue(all(not x.get("instrument_key") for x in un))

    def test_ambiguous_is_unresolved(self):
        rows = rows_for("Nifty 50") + [{"instrument_key": "NSE_INDEX|OTHER", "name": "NIFTY 50", "trading_symbol": "X"}]
        res, un = ix.resolve(rows)
        self.assertNotIn("NIFTY 50", [r["label"] for r in res])
        self.assertIn("not guessing", [u for u in un if u["label"] == "NIFTY 50"][0]["reason"])

    def test_case_and_spacing_do_not_matter(self):
        res, _ = ix.resolve([{"instrument_key": "K1", "name": "  NIFTY   50 ", "trading_symbol": ""}])
        self.assertEqual(res[0]["instrument_key"], "K1")

    def test_match_on_trading_symbol(self):
        res, _ = ix.resolve([{"instrument_key": "K9", "name": "", "trading_symbol": "NIFTY IT"}])
        self.assertEqual([r["label"] for r in res], ["NIFTY IT"])

    def test_two_labels_never_share_one_instrument(self):
        wanted = [("A", ["same"]), ("B", ["same"])]
        res, un = ix.resolve([{"instrument_key": "K", "name": "same", "trading_symbol": ""}], wanted)
        self.assertEqual([r["label"] for r in res], ["A"])
        self.assertIn("same instrument as A", un[0]["reason"])

    def test_the_thirteen_requested_indices(self):
        self.assertEqual([w[0] for w in ix.WANTED], ["NIFTY 50", "BANK NIFTY", "NIFTY NEXT 50", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA",
                                                       "NIFTY FMCG", "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY", "NIFTY INFRASTRUCTURE"])

    def test_no_instrument_key_is_hard_coded(self):
        text = Path("index_data_updater.py").read_text()
        self.assertNotIn("NSE_INDEX|", text.replace("segment", ""))


class Job(unittest.TestCase):
    def setUp(self):
        self.rows = rows_for("Nifty 50", "Nifty Bank")
        self.data = {"NSE_INDEX|Nifty 50": candles(dt.date(2024, 9, 1), END), "NSE_INDEX|Nifty Bank": candles(dt.date(2024, 9, 1), END, 2000.0, 2.0)}

    def go(self, old=None, data=None, **kw):
        return ix.run(self.rows, FakeClient(data or self.data, **kw), old, NOW, END)

    def test_first_run(self):
        doc, problems = self.go()
        self.assertEqual(problems, [])
        self.assertEqual([e["label"] for e in doc["indices"]], ["NIFTY 50", "BANK NIFTY"])
        self.assertEqual((doc["freshness"], doc["interval"], doc["schema_version"], doc["kind"]), ("EOD", "1day", 1, "index_history"))
        self.assertEqual(doc["as_of"], "2026-10-06")
        self.assertEqual(doc["freshness_detail"]["label"], "EOD as of 2026-10-06")
        e = doc["indices"][0]
        self.assertEqual((e["instrument_key"], e["last_date"], e["freshness"], e["source_is_latest_trading_day"]), ("NSE_INDEX|Nifty 50", "2026-10-06", "EOD as of 2026-10-06", True))
        self.assertEqual(sorted(e["candles"][0]), ["close", "date", "high", "low", "open", "volume"])
        self.assertIsNone(e["candles"][0]["volume"])                                  # Upstox sent 0: not 'legitimately available'
        self.assertEqual(len(doc["unresolved"]), 11)
        json.dumps(doc, allow_nan=False)

    def test_latest_summary(self):
        doc, _ = self.go()
        lt = doc["indices"][0]["latest"]
        c = doc["indices"][0]["candles"]
        self.assertEqual((lt["date"], lt["close"], lt["prev_close"]), (c[-1]["date"], c[-1]["close"], c[-2]["close"]))
        self.assertEqual(lt["change_pct"], round((c[-1]["close"] / c[-2]["close"] - 1) * 100, 2))

    def test_latest_summary_edge_cases(self):
        self.assertIsNone(ix.latest_summary([]))
        one = ix.latest_summary([{"date": "2026-10-06", "close": 5.0}])
        self.assertEqual((one["prev_close"], one["change_pct"]), (None, None))
        gap = ix.latest_summary([{"date": "2026-09-01", "close": 5.0}, {"date": "2026-10-06", "close": 6.0}])
        self.assertIsNone(gap["change_pct"])

    def test_history_window(self):
        doc, _ = self.go()
        first = doc["indices"][0]["first_date"]
        self.assertGreaterEqual(first, (END - dt.timedelta(days=ix.KEEP_DAYS)).isoformat())

    def test_positive_volume_is_kept(self):
        self.data["NSE_INDEX|Nifty 50"][-1]["volume"] = 1234
        doc, _ = self.go()
        self.assertEqual(doc["indices"][0]["candles"][-1]["volume"], 1234)

    def test_incremental_refetch(self):
        doc, _ = self.go()
        c = FakeClient(self.data)
        doc2, p = ix.run(self.rows, c, doc, NOW, END)
        self.assertEqual(p, [])
        self.assertLessEqual(len(c.calls), 4)                                         # only the tail, not 2 years again
        self.assertEqual(doc2["indices"][0]["candles"], doc["indices"][0]["candles"])

    def test_fetch_error_writes_nothing(self):
        doc, problems = self.go(fail_for="NSE_INDEX|Nifty Bank")
        self.assertIsNone(doc)
        self.assertTrue(any("HTTP 500" in x for x in problems))

    def test_no_resolved_index_fails(self):
        doc, problems = ix.run(rows_for("Something Else"), FakeClient({}), None, NOW, END)
        self.assertIsNone(doc)
        self.assertTrue(problems)

    def test_stale_candles_fail(self):
        data = {"NSE_INDEX|Nifty 50": candles(dt.date(2024, 9, 1), dt.date(2026, 9, 1)), "NSE_INDEX|Nifty Bank": self.data["NSE_INDEX|Nifty Bank"]}
        doc, problems = self.go(data=data)
        self.assertIsNone(doc)
        self.assertTrue(any("days old" in x for x in problems))

    def test_going_back_in_time_refused(self):
        doc, _ = self.go()
        doc["as_of"] = "2026-10-20"
        doc2, problems = self.go(old=doc)
        self.assertIsNone(doc2)
        self.assertTrue(any("back in time" in x for x in problems))

    def test_published_index_may_not_silently_disappear(self):
        doc, _ = self.go()
        self.rows = rows_for("Nifty 50")                                              # Bank Nifty can no longer be resolved
        doc2, problems = self.go(old=doc)
        self.assertIsNone(doc2)
        self.assertTrue(any("refusing to drop" in x for x in problems))

    def test_changed_instrument_key_is_never_mixed(self):
        doc, _ = self.go()
        self.rows = [{"instrument_key": "NSE_INDEX|NEW", "name": "Nifty 50", "trading_symbol": ""}, {"instrument_key": "NSE_INDEX|Nifty Bank", "name": "Nifty Bank", "trading_symbol": ""}]
        self.data["NSE_INDEX|NEW"] = candles(dt.date(2024, 9, 1), END, 5000.0, 1.0)
        c = FakeClient(self.data)
        doc2, p = ix.run(self.rows, c, doc, NOW, END)
        new = {c["date"]: c["close"] for c in self.data["NSE_INDEX|NEW"]}
        got = doc2["indices"][0]["candles"]
        self.assertTrue(all(new[c["date"]] == c["close"] for c in got), "no candle of the old instrument may survive under the new key")
        self.assertGreater(len(got), 300)
        self.assertEqual(doc2["indices"][0]["instrument_key"], "NSE_INDEX|NEW")

    def test_check_doc_rules(self):
        doc, _ = self.go()
        import copy
        for name, fn in {
            "order": lambda d: d["indices"][0]["candles"].reverse(),
            "future": lambda d: d["indices"][0]["candles"][-1].__setitem__("date", "2999-01-01"),
            "zero price": lambda d: d["indices"][0]["candles"][3].__setitem__("close", 0),
            "high<low": lambda d: d["indices"][0]["candles"][3].update({"high": 1, "low": 5}),
            "negative volume": lambda d: d["indices"][0]["candles"][3].__setitem__("volume", -1),
            "duplicate": lambda d: d["indices"].append(copy.deepcopy(d["indices"][0])),
            "nan": lambda d: d["indices"][0]["candles"][3].__setitem__("close", float("nan")),
            "empty": lambda d: d["indices"][0].__setitem__("candles", []),
        }.items():
            d = copy.deepcopy(doc)
            fn(d)
            self.assertTrue(ix.check_doc(d, NOW.date()), name)
        self.assertEqual(ix.check_doc(doc, NOW.date()), [])
        self.assertTrue(ix.check_doc({"indices": []}, NOW.date()))

    def test_no_token_in_module(self):
        text = Path("index_data_updater.py").read_text()
        self.assertNotIn("print(token", text)
        self.assertNotIn("Bearer", text)


if __name__ == "__main__":
    unittest.main()
