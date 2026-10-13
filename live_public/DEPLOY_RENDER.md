# Deploying the StockLens live backend on Render

Status: **prepared, not deployed.** Nothing here creates a service by itself: `render.yaml` sets `autoDeployTrigger: "off"`, the snapshot gate ships as `"false"`, and the website's `PUBLIC_LIVE` switch is off.

## Files
| File | Purpose |
|---|---|
| `render.yaml` | Blueprint: one Python web service, `python -m live_public`, health check `/healthz/live`, all settings except the token |
| `requirements.txt` | `websockets==15.0.1`, `requests==2.34.2` (the only third-party imports in `live_public/`, `live_service/`, `live/`; a test checks this) |
| `live_public/` | the service (`config.py`, `server.py`, `__main__.py`) |

## The two health endpoints
- `/healthz/live` (Render's health check): `200 {"status":"alive"}` while the HTTP service is running. It never looks at the relay or the feed, is exempt from the Host, Origin and rate-limit checks, and sends no CORS grant. A Upstox outage, a rejected token or a shut gate cannot make it fail, so Render never restarts a working process because of the feed.
- `/healthz` (reporting; use it by hand or from a monitor): `{"status":"ok"|"unavailable","snapshot":"disabled"|"none"|"fresh"|"stale"|"invalid"}`, HTTP 200 or 503. Host-checked and rate-limited like the snapshot.

## What Render documents (read 13 Oct 2026)
- Web services must bind `0.0.0.0` on `PORT` (default 10000). The service does: `HOST=0.0.0.0`, `PORT=10000`.
- Health check: HTTP GET to `healthCheckPath`; any 2xx/3xx is healthy, 4xx/5xx unhealthy; 5 s timeout. The `Host` header is the service's `onrender.com` name (or a verified custom domain). A new deploy goes live only if all instances pass; after 15 minutes of failing it is cancelled. A running instance that fails for 60 s is restarted.
- Web services can make outbound WebSocket connections; there is no maximum connection duration. Connections close when the instance is replaced (deploys, restarts): SIGTERM, then 30 s to stop. The service handles SIGTERM.
- Paid instances run continuously. A free instance spins down after 15 minutes without inbound traffic and may restart at any time, so it is suitable only for a short test.
- `PYTHON_VERSION` must be a full version (e.g. `3.13.5`). Render's default for new services is newer than the version this code was tested on, so the Blueprint pins it.
- `RENDER_EXTERNAL_HOSTNAME` is set to the service's `onrender.com` name. With `ALLOWED_HOSTS` unset the service allows exactly that name, which is also the Host Render's health check sends. The liveness path does not check Host at all, so the deploy cannot fail on a hostname mismatch; `/healthz` and the snapshot do check it.
- **Not documented:** how Render forwards the visitor's address. A community report says the proxy appends to a client-supplied `X-Forwarded-For`, so no entry can be trusted blindly. Therefore `LIVE_PUBLIC_TRUST_PROXY=false` and all visitors share one rate-limit bucket (see Risks).

## Environment variables
| Variable | Value | Where |
|---|---|---|
| `UPSTOX_ANALYTICS_TOKEN` | your Upstox analytics token | **dashboard only** (prompted at Blueprint creation); never in the repository, chat or logs |
| `LIVE_PUBLIC_SERVE_SNAPSHOT` | `false` in the file; set `true` in the dashboard for the real-feed test | gate: with anything but exactly `true` the relay is not started and no Upstox connection is made |
| `ALLOWED_ORIGIN` | `https://arnavdev11.github.io` | file |
| `ALLOWED_HOSTS` | not set (uses `RENDER_EXTERNAL_HOSTNAME`) | set only for a custom domain; list the `onrender.com` name too |
| `HOST`, `PORT` | `0.0.0.0`, `10000` | file |
| `LIVE_PUBLIC_TRUST_PROXY` | `false` | file |
| `LIVE_PUBLIC_RATE_PER_MINUTE`, `LIVE_PUBLIC_BURST`, `LIVE_PUBLIC_MAX_CONNECTIONS`, `LIVE_PUBLIC_STALE_AFTER_S` | `600`, `200`, `200`, `30` | file |
| `PYTHON_VERSION` | `3.13.5` | file |

## Dashboard steps for the first real-feed test
Prerequisite (needs your approval, not done): the branch holding these files must be committed and pushed to GitHub, because Render builds from a Git branch.
1. Render dashboard -> **New** -> **Blueprint** -> connect the `Arnavdev11/stock` repository -> choose the branch that holds `render.yaml` -> Render reads `render.yaml`.
2. When prompted, paste `UPSTOX_ANALYTICS_TOKEN`. Review the plan shown (`0.5c-512mb`, paid) and create the service. Do not change `LIVE_PUBLIC_SERVE_SNAPSHOT` yet.
3. Wait for the first deploy to pass its health check (`/healthz/live`). Note the service address shown at the top of the service page (`https://<name>.onrender.com`).
4. Gate-off check (any time): `curl -s https://<host>/healthz/live` -> `{"status":"alive"}`; `curl -s https://<host>/healthz` -> `{"status":"ok","snapshot":"disabled"}` and `curl -s -i https://<host>/v1/live/snapshot.json` -> HTTP 503 `{"error":"snapshot_disabled"}`. The logs say "the relay is not started".
5. During NSE hours (relay window 08:55-15:45 IST): service -> **Environment** -> set `LIVE_PUBLIC_SERVE_SNAPSHOT` to `true` -> save (Render redeploys).
6. Watch **Logs** for "snapshot serving is ON: starting the relay" and, within a minute, no ERROR lines. Watch **Metrics -> Memory** during the start (see Risks).
7. Check: `curl -s https://<host>/healthz` -> `{"status":"ok","snapshot":"fresh"}`; `curl -s -H "Origin: https://arnavdev11.github.io" -i https://<host>/v1/live/snapshot.json` -> HTTP 200, `Access-Control-Allow-Origin: https://arnavdev11.github.io`, a `Date` header, JSON with `scope.universe` `test_set`, `quality.ok` true, `breadth.counted` 0, `gainers` null, and `NIFTY 50` plus the five equities. A request with another `Origin` must give 403.
8. To turn the feed off again: set `LIVE_PUBLIC_SERVE_SNAPSHOT` back to `false` (or suspend the service).

If the token is rejected the relay stops retrying ("auth_stopped"), `/healthz` returns 503 `unavailable` and the page would show UNAVAILABLE, while `/healthz/live` stays 200 so Render keeps the process running; fix the variable in the dashboard and restart.

## Where the real HTTPS address goes (frontend)
Only after step 7 succeeds. In `index.html`, inside `<script type="module" id="stocklens-live">`, the single line

    var PUBLIC_LIVE={enabled:false,url:""};

becomes

    var PUBLIC_LIVE={enabled:true,url:"https://<host>.onrender.com/v1/live/snapshot.json"};

using the exact address from step 3. The gate refuses any other shape (http, port, query, IP address, other path) and any page not served from `https://arnavdev11.github.io`. Two guard tests deliberately forbid a web address inside that module and must be updated in the same reviewed change: `tests/test_live_client.js` ("the only web address is the loopback relay") and `test_live_parity.py` (`test_the_block_has_no_token_no_html_sinks_no_outside_address_no_advice`). The site then needs the normal publish ("Update data and publish site") before visitors see it.

## Risks and open items
- **Memory:** at start the service downloads Upstox's public instrument file and parses it in memory before keeping six instruments. Not measured here (no network). If Metrics shows the 512 MB instance near its limit or restarting, move to `1c-2g`.
- **Shared rate limit:** because no visitor address can be trusted, the limits apply to Render's proxy as a whole: about 600 requests a minute, i.e. roughly 100 visitors with the page open (6 requests a minute each). A heavy burst, or one abuser, can make everyone see UNAVAILABLE (the page then shows end-of-day data). Per-visitor limiting needs a verified client-address header.
- **Connection cap:** when the global connection limit is full the server answers 503 `busy` before reading the request, which includes the liveness path. Render restarts an instance only after 60 s of consecutive failures, so only a sustained flood could do that.
- **Upstox terms:** the written confirmation that public redistribution is permitted has not been seen by this repository's records. Keep it on file before enabling the page.
- **Holidays:** `data/holidays.txt` is not in the repository, so only weekends count as closed; on an exchange holiday the relay connects and reports what the exchange's market status says.
- **Real feed untested:** the real WebSocket path (`websockets` 15.0.1) has never run in this environment; the first run is the test.
