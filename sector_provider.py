"""
sector_provider.py - StockLens sector architecture, Phase 1: PROVIDER ABSTRACTION.

Everything that knows where a sector label comes from lives here. The master updater and the dashboard calculation only see the
SectorProvider interface, so a different (licensed) provider can replace Upstox by adding one class.

Interface:
    name                      short provider id stored in every record
    universe()                -> list of {"isin", "symbol", "company_name", "exchange"}   (the instruments to classify)
    sector_for(isin)          -> (label or None, answered)    answered=False means "no answer" (error / limit), not "no sector"

Provided here:
    UpstoxProvider            Upstox instrument file + fundamentals profile endpoint, via the existing upstox_common helpers
    FileProvider              reads a local JSON file in the shape below (for a licensed file delivery, or for tests)
        {"provider": "<id>", "instruments": [{"isin","symbol","company_name","exchange","sector"}]}
No provider here is allowed to invent or infer a sector.
"""
import json
from pathlib import Path


class SectorProvider:
    name = "base"

    def universe(self):
        raise NotImplementedError

    def sector_for(self, isin):
        raise NotImplementedError


class FileProvider(SectorProvider):
    def __init__(self, path):
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.name = str(doc.get("provider") or "file")
        self._items = [x for x in doc.get("instruments", []) if isinstance(x, dict)]

    def universe(self):
        return [{"isin": x.get("isin"), "symbol": x.get("symbol"), "company_name": x.get("company_name"),
                 "exchange": x.get("exchange") or "NSE"} for x in self._items]

    def sector_for(self, isin):
        for x in self._items:
            if x.get("isin") == isin:
                return x.get("sector") or None, True
        return None, False


class UpstoxProvider(SectorProvider):
    """Uses upstox_common (shared with the other updaters). The token is read from the environment by get_token() and is never stored."""
    name = "upstox"

    def __init__(self, max_calls=1500, client=None, instruments=None):
        import upstox_common as uc            # imported here so that importing this module needs no network library
        self.uc = uc
        self._instruments = instruments
        self._client = client
        self.max_calls = max_calls
        self.errors = 0

    def universe(self):
        inst = self._instruments if self._instruments is not None else self.uc.load_instruments()
        return [{"isin": v["isin"], "symbol": s, "company_name": v.get("name"), "exchange": "NSE"}
                for s, v in sorted(inst.items())]

    def sector_for(self, isin):
        if self._client is None:
            self._client = self.uc.Upstox(self.uc.get_token(), self.max_calls)
        body, err = self._client.get(isin + "/profile")
        if err or not isinstance(body, dict):
            self.errors += 1
            return None, False
        data = body.get("data")
        if not isinstance(data, dict):           # a garbled or empty reply is "no answer", never "this stock has no sector"
            self.errors += 1
            return None, False
        label = data.get("sector")
        label = label.strip() if isinstance(label, str) else None
        return (label or None), True
