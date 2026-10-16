"""
Tests for the all-stock dated last close that the Stock Detail page reads: out/market_dma.json (market_data_updater.py -> stocks[]).
Run:  python3 -m unittest test_last_close_source -v      (no network; every file is built in a temporary folder)

The page (index.html, lastClose / eodClose) relies on exactly this contract: kind "market_dma", as_of = the latest trading day in the NSE file, and one stocks[] entry per
EQ-series symbol traded on that day, carrying that day's close. These tests pin it down with a fully known market, independently of the code under test.
"""
import unittest

import market_data_updater as mu
import validate_market_outputs as vmo
from test_market_data import World, Tmp, NOW, HEADER


class LastCloseSource(Tmp):
    def build(self, extra_rows=None, drop=None):
        w = World()
        last = w.n - 1
        rows = [w.row(s, last) for s in w.symbols() if s != drop]
        rows += extra_rows or []
        w.rows_override = {last: rows}
        docs, w = self.docs(w)
        return docs["dma"], w

    def test_every_eq_stock_has_a_dated_close(self):
        d, w = self.build()
        self.assertEqual(d["kind"], "market_dma")
        self.assertEqual(d["as_of"], w.dates[-1].isoformat())
        got = {r["symbol"]: r["close"] for r in d["stocks"]}
        self.assertEqual(set(got), set(w.symbols()), "one entry for every EQ symbol traded on the latest day")
        for s in w.symbols():
            self.assertEqual(got[s], w.close[s][-1], s + ": the entry carries the latest day's close, unadjusted")
        self.assertEqual(len(got), len(d["stocks"]), "each symbol appears once")

    def test_close_is_a_finite_positive_number(self):
        d, _ = self.build()
        for r in d["stocks"]:
            self.assertIsInstance(r["close"], float)
            self.assertGreater(r["close"], 0)

    def test_only_eq_series_is_covered(self):
        be = ["BESTOCK", "BE", self.world_date(), 99.0, 100.0, 101.0, 99.0, 100.0, 100.0, 100.0, 300000, 300.0, 1000, 1000, 40.0]
        d, w = self.build(extra_rows=[be])
        self.assertNotIn("BESTOCK", {r["symbol"] for r in d["stocks"]}, "a BE-series stock has no close in this file: the page must say Unavailable, not guess")

    def test_a_symbol_not_traded_on_the_latest_day_is_absent_not_carried_forward(self):
        d, w = self.build(drop="SHORT")
        self.assertNotIn("SHORT", {r["symbol"] for r in d["stocks"]}, "an older close is never presented under the latest date")

    def test_the_document_passes_the_published_schema(self):
        d, w = self.build()
        docs, _ = self.docs(w)
        self.assertEqual(vmo.validate(docs), [])

    def world_date(self):
        return World().dates[-1].strftime("%d-%b-%Y")


if __name__ == "__main__":
    unittest.main()
