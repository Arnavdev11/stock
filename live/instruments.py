"""
live/instruments.py - what to subscribe to, and what each instrument key means.

Pure: it is given the rows of the Upstox NSE instrument file (already downloaded and parsed by the caller) and returns maps. No network, no stock list in
code: every symbol comes from the rows. Indices are matched by EXACT name, and only when exactly one instrument has that name (nothing is guessed).
"""
import json

# label shown on the site -> exact instrument names to look for (lower-case); the same labels as the end-of-day index history
WANTED_INDICES = [
    ("NIFTY 50", ["nifty 50"]),
    ("BANK NIFTY", ["nifty bank", "bank nifty"]),
    ("NIFTY NEXT 50", ["nifty next 50"]),
    ("NIFTY MIDCAP 100", ["nifty midcap 100"]),
    ("NIFTY SMALLCAP 100", ["nifty smallcap 100", "nifty smlcap 100"]),
    ("NIFTY IT", ["nifty it"]),
    ("NIFTY AUTO", ["nifty auto"]),
]
CHUNK = 500


class Instruments:
    def __init__(self, equities, indices, unresolved):
        self.equities = equities          # instrument_key -> symbol
        self.indices = indices            # instrument_key -> label   (insertion order = display order)
        self.unresolved = unresolved      # label -> reason

    @classmethod
    def from_rows(cls, rows, wanted=None):
        wanted = WANTED_INDICES if wanted is None else wanted
        eq, idx_rows = {}, []
        seen_sym = {}
        for x in rows:
            if not isinstance(x, dict) or not x.get("instrument_key"):
                continue
            seg = x.get("segment")
            if seg == "NSE_EQ" and x.get("instrument_type") == "EQ" and x.get("trading_symbol"):
                sym = str(x["trading_symbol"]).strip().upper()
                if sym in seen_sym:                        # two instruments claiming one symbol: neither is trusted
                    eq.pop(seen_sym[sym], None)
                    continue
                seen_sym[sym] = x["instrument_key"]
                eq[x["instrument_key"]] = sym
            elif seg == "NSE_INDEX":
                idx_rows.append((str(x.get("name") or x.get("trading_symbol") or "").strip().lower(), x["instrument_key"]))
        indices, unresolved = {}, {}
        for label, names in wanted:
            hits = sorted({k for n, k in idx_rows if n in names})
            if len(hits) == 1:
                indices[hits[0]] = label
            else:
                unresolved[label] = "%d instruments match" % len(hits)
        return cls(eq, indices, unresolved)

    def symbol_of(self, key):
        return self.equities.get(key)

    def kind_of(self, key):
        if key in self.indices:
            return "index"
        if key in self.equities:
            return "equity"
        return None

    def index_label(self, key):
        return self.indices.get(key)

    def keys_for(self, symbols=None, with_indices=True):
        """Subscription keys: all indices (first, always), then the chosen equities (all when symbols is None). Sorted for a reproducible order."""
        ks = list(self.indices) if with_indices else []
        if symbols is None:
            ks += sorted(self.equities)
        else:
            want = {s.upper() for s in symbols}
            ks += sorted(k for k, s in self.equities.items() if s in want)
        return ks


def chunks(keys, n=CHUNK):
    return [keys[i:i + n] for i in range(0, len(keys), n)]


def liquid_symbols_from_dma(doc):
    """The liquid-stock list from the published end-of-day out/market_dma.json (stocks[].liquid). Anything unexpected -> empty set (then no live movers
    are shown: a live list must not use a different, unfiltered universe than the end-of-day list)."""
    try:
        return {s["symbol"] for s in doc["stocks"] if isinstance(s, dict) and s.get("liquid") is True and isinstance(s.get("symbol"), str)}
    except (KeyError, TypeError):
        return set()


def load_liquid(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return liquid_symbols_from_dma(json.load(f))
    except (OSError, ValueError):
        return set()
