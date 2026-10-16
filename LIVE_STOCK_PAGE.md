# Stock Detail live strip: how it stays safe, how to verify it, how to scale it

Status: **local proposal, not enabled.** Nothing here changes the live service, Render, tokens, subscriptions or the public page. By default a visitor sees the dated NSE close only.

## What it is
`index.html` module `stocklens-stocklive` draws one line under the dated-close note on `#stock=SYMBOL` pages:

| Shown | Meaning |
|---|---|
| `LIVE · ₹… · +x.xx% vs previous close · last trade HH:MM:SS IST · snapshot n s old · limited test feed` | a valid, current snapshot contains this symbol and it traded within 2 minutes of the snapshot |
| `STALE · not current · last live ₹… at HH:MM:SS IST (n s ago) · reason` | the last valid answer is older than 20 s but at most 15 minutes old, or the stock has not traded for 2 minutes |
| `LAST CLOSE — 15 Oct 2026 · no live price: reason` | everything else: market closed, no answer, invalid data, stock not in the snapshot, answer older than 15 minutes |

The "Latest Price" tile and the dated close note are never replaced: the strip sits beside them.

## Safety checks (what is reused, what is added)
Reused unchanged from the existing live layer (`Controller.ingest`, `validate`, `accepts`): service Date header present and within 60 s of the device clock; strict schema; `quality.ok`; market `open`; snapshot age at most 20 s; refusal of a `test_set` snapshot unless the explicit test gate is on; public feed limited to the test set (no breadth, no movers); fetch with no credentials, no redirects, no cache, 4 s timeout, 64 KB cap.

Added, because the schema validators (`live/snapshot.py:validate_snapshot` and the page's `validate`) check only the symbol and a numeric `ltp` for a prices entry:
1. the symbol must be an own key of `prices` (no prototype names, no case folding);
2. `ltp` finite and above zero; `cp` absent or above zero;
3. `change_pct` equals the value recomputed from `ltp` and `cp` (0.01) and the move is within 40%;
4. `ltt` is a real `+05:30` timestamp, on the snapshot's `market.session_date`, not later than the snapshot (5 s allowance);
5. age = service clock age + time since arrival; LIVE needs at most 20 s; the stock must have traded within 120 s of the snapshot;
6. a repeated poll that fails keeps the last valid answer as STALE for at most 15 minutes, then LAST CLOSE.

## The `test_set` protection is not bypassed
The deployed service labels its snapshot `test_set`. The page refuses such a snapshot unless the existing explicit test gate is on (`?live=public_test`, or `PUBLIC_LIVE` after a reviewed change). The strip is inert unless that gate is on (`window.SLLive.active()`), makes no request of its own and adds no address. Showing live prices to every visitor needs a separate decision (see "Open questions").

Intended future checks before public display (not implemented): a non-`test_set` universe label for the six-stock set agreed with the service; a per-symbol freshness field produced by the service; the Upstox permission scope (educational, no ads, no paid access) restated on the page.

## Verify end to end (only after separate approval)
1. Confirm the Render service is awake and `/healthz` says `snapshot: fresh` during market hours (08:55-15:45 IST).
2. Open `https://arnavdev11.github.io/stock/?live=public_test#stock=TCS` (a deployed build containing this module is required).
3. Expect `LIVE` within one poll (10 s); the trade time must change between two polls a minute apart. A value that does not change is not proof; compare with a second source at the same minute.
4. Open `#stock=SBIN`: expect `LAST CLOSE ... not in the live set`.
5. Stop the service (or wait for the free plan to sleep): within about 30 s expect `STALE`, after 15 minutes `LAST CLOSE`.
6. Outside market hours: expect `LAST CLOSE ... market is closed`.
7. Without `?live=public_test`: no strip, no request to the service (browser network tab).
Record each result with a timestamp. Do not enable `PUBLIC_LIVE` as part of this check.

## Staged scaling plan (100 / 500 / ~2,500 instruments)
Nothing here is measured yet; the six-instrument baseline observed on the free plan was 62.5 MB memory of 512 MB (limit 0.15 CPU). Each stage is a separate approval, a paid always-on plan, and a flag; stop at the first failed gate.

| Stage | Instruments | Snapshot size (raw JSON of the prices block, estimate from one entry of ~93 bytes) | Browser limits hit |
|---|---|---|---|
| 0 | 6 | ~0.6 KB | none |
| 1 | 100 | ~9 KB | none |
| 2 | 500 | ~46 KB | near the 64 KB cap in `CFG.maxBytes` once other blocks are added; `prices` is capped at 200 entries |
| 3 | ~2,500 | ~230 KB | over both the 200-entry and 64 KB caps: needs per-symbol or sharded delivery, a design change |

Gates for every stage (measure over at least 3 market hours, normal and busy):
- **Memory:** Render memory_usage below 70% of the limit at peak; trend flat.
- **CPU:** average below 60% and no sustained throttling of the 0.15 CPU free tier (a paid plan is expected from Stage 1).
- **Tick freshness:** coverage (`scope.ticked / scope.subscribed`) at least 0.90 while open; p95 of (`server_ts_ms - ltt`) under 120 s for liquid stocks; snapshot `age` under 20 s at p99 as seen by the page.
- **Dropped frames:** the relay exposes `frames`, `decode_errors`, `text_frames`, `connects` and `connect_failed` counters but no explicit dropped-frame counter. Use: zero decode errors, no reconnect during open hours, and the longest gap between frames under 10 s (`frame_gap_ms`). A true dropped-frame counter is an open item.
- **Snapshot size:** raw and gzip bytes per response, against the 64 KB browser cap; request rate against `rate_per_minute=600`.
- **Provider:** subscribe in 500-key frames (existing), within the documented 5,000 `ltpc` keys per connection; WebSocket v3 access for the account is unverified at this size.
Rollback at any stage: return the subscription list to the previous stage's size.

## Open questions
See the report that accompanied this change.
