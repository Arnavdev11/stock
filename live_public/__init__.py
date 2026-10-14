"""
live_public - the HOSTED live-market backend (separate from live_service, the local-only relay, which is not touched).

  config.py    the environment-only configuration: fail-closed validation, exact origins, no secret in any text it produces
  server.py    the public HTTPS-side HTTP service (stage 2)
  __main__.py  python -m live_public (stage 3)

It reuses the tested relay (live_service.relay, upstox, safety) unchanged. Nothing here is deployed or enabled: snapshots are not served unless
LIVE_PUBLIC_SERVE_SNAPSHOT is exactly "true", and the Upstox token exists only in this process's memory.
"""
