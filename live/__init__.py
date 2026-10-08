"""
StockLens live-data relay core (Phase 1: OFFLINE components only).

Pure Python, standard library only. No network, no token, no clock of its own (the caller passes "now"), no import of any EOD updater.
    decoder.py       Upstox Market Data Feed V3 protobuf frames -> plain dicts (strict; malformed input raises DecodeError, never anything else)
    instruments.py   instrument-file rows -> key/symbol maps, subscription chunks, the liquid-stock list
    ticks.py         the validated in-memory tick store (bad-tick rules)
    market_hours.py  market status from the clock, the holiday list and the feed's own segment status
    snapshot.py      the browser snapshot: builder, strict encoder, validator, and the browser's accept rules (mirrored in Python for tests)
    state.py         the reconnect / recovery state machine (pure: events in, actions out)
    replay.py        frame encoder, synthetic market, recorder / replayer for the tests
Later phases add the network feed client and the HTTP server around these parts; they do not change them.
"""
