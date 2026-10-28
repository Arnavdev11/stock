# Operational readiness (measured on the data branch at `ea95074`, universe as of 2026-10-15, report date 2026-10-25)

Both automatic datasets are **ready for a manually supervised pilot** and **not ready for automatic scheduling**. "Ready" means: measurable, durable, verifiable. It does not mean complete.

| | Historical candles | Financial history |
|---|---|---|
| Coverage now | 15 of 2,548 (0.59%) | 793 of 2,548 (31.12%) |
| Missing work | 2,533 never fetched | 1,755 not in the ledger (3 entries have no years; 2 have a recorded fetch error) |
| Calls still needed | 15,198 first-fill calls (6 per stock) | 3,510 (2 per stock; up to 4 with a standalone fallback) |
| Calls per run (default / ceiling) | 150 / 3,000 (`CALL_CEILING`; 10% headroom kept, benchmark reserved) | 120 / 1,500 (this tool's own cap; the updater has none) |
| Stocks reached per run | about 22 at 150 calls; 449 at the ceiling | about 60 at 120 calls |
| Runs at the default budget | about 114 | about 30 |
| Runs at the ceiling | about 6 | about 3 |
| Pacing / time | 1 s pause, planned at 2.0 s per call; 3,000 calls = 100 min in a 120-min job | 2 s pause, about 2.3 s per call measured; 1,500 calls = 58 min (172 min if every call is retried 3 times) in a 240-min job |
| Retries / cooldown | 3 attempts per request; a stock a batch could not save is not planned again for 3 days (`attempts.json`) | 3 attempts per request; no attempts counter: a failing stock sorts behind the others, never marked unavailable |
| Progress stored | data branch `historical/` (shards, `historical.json`, `attempts.json`) via `save_historical.yml` | data branch `financial_history_ledger.json` via `save_ledger.yml` |
| Interrupted run | nothing saved is lost; the hand-over is checked and saved whole or not at all; unfinished stocks stay stale / never fetched | same; the updater's no-loss check runs before anything is written |
| Verification | `verify`: every file and the benchmark, count and last date, `attempts.json` | `verify`: the repository's own no-loss check over every stock, plus cohort outcomes |

## Remaining blockers before the first pilot
1. PRs #42, #43, #44 reviewed and merged in order, and the helper workflow merged to `main` (a manually dispatched workflow must be on the default branch before it can be started).
2. Someone decides the pilot size (suggested: one candle run at the default 150 calls and one financial-history run at 120 calls, supervised, with `verify` after each).
3. The Upstox token and the save workflows are assumed healthy; this review did not exercise them.
4. Optional: a reviewed NIFTY 500 priority list from NSE's official file. Not bundled, not required.

## What is not ready
* Fundamentals, quarterly results, annual financials: not measurable offline and not durable (see `BACKFILL_STATE.md`).
* Any scheduled or self-dispatching continuation: not built, not approved.
* The platform is not complete while any dataset is unmeasurable.
