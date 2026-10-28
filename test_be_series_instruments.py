"""The Upstox instrument loaders read NSE series EQ and BE (the two series the stock directory admits), not EQ alone.
Found during the 28 Oct 2026 candle backfill: every planned BE stock (3IINFOLTD, AARNAV, ABAN, ...) was skipped as "not served by Upstox", and none of them
was in the financial-history ledger either, because every loader kept instrument_type == "EQ" only. Offline: requests.get is replaced.
Run: python3 -m unittest test_be_series_instruments"""
import json
import unittest
from unittest import mock

import fundamentals_updater as fu
import historical_updater as hu
import upstox_common as uc


def row(sym, itype, isin, segment="NSE_EQ"):
    return {"segment": segment, "instrument_type": itype, "trading_symbol": sym, "isin": isin, "name": sym + " LTD",
            "instrument_key": "%s|%s" % (segment, isin)}


ROWS = [row("EQCO", "EQ", "INE000000101"), row("BECO", "BE", "INE000000102"), row("BZCO", "BZ", "INE000000103"), row("SMCO", "SM", "INE000000104"),
        row("BOTH", "BE", "INE000000106"), row("BOTH", "EQ", "INE000000105"), row("BOTH2", "EQ", "INE000000107"), row("BOTH2", "BE", "INE000000108"),
        row("NIFTY", "INDEX", "", segment="NSE_INDEX") | {"name": "Nifty 50", "instrument_key": "NSE_INDEX|Nifty 50"}]


class Resp:
    status_code = 200

    def __init__(self, rows):
        self.content = json.dumps(rows).encode()

    def raise_for_status(self):
        pass


class Loaders(unittest.TestCase):
    def check(self, found):
        self.assertEqual(sorted(found), ["BECO", "BOTH", "BOTH2", "EQCO"])            # BZ (excluded series) and SM (SME) stay out
        self.assertEqual(found["BECO"]["isin"], "INE000000102")
        self.assertEqual(found["BOTH"]["isin"], "INE000000105")                       # EQ wins over BE whatever the row order
        self.assertEqual(found["BOTH2"]["isin"], "INE000000107")

    def test_upstox_common(self):
        with mock.patch.object(uc.requests, "get", return_value=Resp(ROWS)):
            self.check(uc.load_instruments())

    def test_fundamentals_updater(self):
        with mock.patch.object(fu.requests, "get", return_value=Resp(ROWS)):
            self.check(fu.load_instruments())

    def test_historical_updater(self):
        with mock.patch.object(hu.requests, "get", return_value=Resp(ROWS)):
            eq, idx = hu.load_instrument_file()
        self.check(eq)
        self.assertEqual(eq["BECO"]["instrument_key"], "NSE_EQ|INE000000102")
        self.assertEqual([i["name"] for i in idx], ["Nifty 50"])


if __name__ == "__main__":
    unittest.main()
