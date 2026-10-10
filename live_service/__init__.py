"""
live_service - Phase 2: the LOCAL relay service. It connects to the Upstox market data feed (WebSocket V3) from the machine it runs on, feeds the real ticks
through the Phase 1 core (live/), and serves the resulting snapshot on 127.0.0.1 only.

  safety.py   secret scrubbing, the log, loopback checks
  upstox.py   authorization, instrument lookup, the WebSocket transport (the only places that touch the network, both behind small replaceable interfaces)
  relay.py    the service: supervisor actions -> network calls, frames -> tick store -> snapshots, health
  server.py   the two local HTTP endpoints
  __main__.py command line:  python -m live_service

Nothing here writes a file, and the Upstox token exists only in this process's memory.
"""
