"""
research_test_batch.py - the five-stock test batch for the fundamentals and financial-statement updaters ONLY.

Why: with no universe.json on the data branch, both updaters fall back to the 10 development stocks, so a stock that is only in the stock directory (DOMS, say)
is never requested and its detail page has no company, ratios or statements. This adds five named stocks to the development set for those two updaters, so the
whole path (Upstox -> cache -> out/*.json -> page) can be checked on real responses before any wider coverage is decided.

What it does NOT do:
  * it is not a universe: no file is written, nothing else reads it, and the other updaters (historical, financial history, shareholding, prices) never import it;
  * it does nothing when a universe.json is configured (STOCKLENS_UNIVERSE_FILE): a real universe always wins;
  * it changes no budget, cache rule, pacing or error handling, and it adds no data: a symbol Upstox cannot serve is reported by the updater like any other;
  * it does not touch the sector gate: these five are not development stocks, so their sector stays unpublished (fundamentals_updater.py).
To end the test, delete the two calls to extend() in fundamentals_updater.py and financials_updater.py (and this file).
"""
import universe

SYMBOLS = ("DOMS", "20MICRONS", "21STCENMGM", "360ONE", "3BBLACKBIO")


def extend(symbols, env=None):
    """symbols (a list, in order) + the test batch (those not already there). Unchanged when a universe file is configured."""
    out = list(symbols)
    if universe.configured_path(env):
        return out
    return out + [s for s in SYMBOLS if s not in out]
