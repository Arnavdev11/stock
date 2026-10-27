# StockLens EOD data pipeline: progress report

Branch `feat/nse-eod-coverage` (from `main` at `92c0fe0`). Local only: nothing pushed, merged, deployed or refreshed. No external API call has been made.
Baseline data: `stocklens-data` at `19c7c07` (27 Oct 2026 16:19 UTC), measured offline with `coverage_report.py --as-of 2026-10-27`.
The request plan awaiting approval is in `API_REQUEST_PLAN.md`.

## Phases

| Phase | Status | Commits |
| --- | --- | --- |
| 0. Indicator readiness measured from the page's own minimums | Done | `049ecf6` |
| 1. Directory audit: symbols, ISIN check digits, instrument keys, cross-file identity | Done | `8047c40` |
| 2. Price history: scheduled refresh budget, storage, backfill plan | Done | `3f88c26` |
| 3. Fundamentals fixes in the existing updaters | Done | `efb2c59` |
| 4. New Upstox endpoints (corporate actions, share holdings), offline | Done | `b3c4810` |
| 5. Frontend: Dividends & Corporate Actions section | Done | `04fcfbb`, `c5d575f` |
| 5b. Coverage report modules for the new data | Done | `bb349b7` |
| 6. Consolidated API request plan; probe extended for the pilot | Done, awaiting approval | `3166534`, plan in this commit |
| 7. Final validation | Done offline (below) | |

## Results per phase

**Phase 0.** `coverage_report.py` counts an indicator only when the saved candles meet the page's own minimums (chart 30, SMA/EMA 200 = 200, MACD signal 34,
ATR 14, volume ratio 21, 52-week range 365 days). `tests/test_indicator_readiness.js` runs the page's functions to keep the numbers equal (41 checks).

**Phase 1.** 2,548 directory stocks, all valid (ISO 6166 check digits correct). Instrument key `NSE_EQ|<ISIN>` derivable for all. 8 saved records carry another ISIN of the
same issuer (shareholding: 360ONE, HDFCBANK, ICICIBANK, INFY, ITC, SBIN; annual history: BANSALWIRE, CCAVENUE). Reported, never remapped.

**Phase 2.** The scheduled price refresh had a fixed 150-call budget, so once filled most saved stocks would go stale. `refresh_budget()` now sizes it to every saved stock
(needed / 0.9, ceiling 3,000), and an explicit `HISTORICAL_MAX_CALLS` stays a hard limit. Storage measured at 94.3 bytes per candle: about 301 MB for 2,548 stocks x 5 years.

**Phase 3.** Annual history: due standalone basis checks (11 stocks, e.g. AUBANK) now run before never-fetched stocks; before, they waited about three weeks.
Correction: an earlier note called the financial-history `missing` list a defect. It is by design: it lists core fields only, and the validator enforces that.
Deferred until real responses: reading `sector_value` from key-ratios (a schema change across validators) and the revenue line for banks/NBFCs.

**Phase 4.** `fundamentals_extra_updater.py`: corporate actions and Upstox share holdings, parsers for the documented shape only, a resumable cache, statuses
(ok, none_reported, unusable, failed, not_in_upstox, never_fetched), bounded back-off, a call budget, per-stock files, summaries and pre-write validation.
A missing amount or category stays empty; the 12-month dividend is left empty with its reason rather than under-counted. Not wired into any workflow (a test asserts it).

**Phase 5.** Stock Detail gains "Dividends & Corporate Actions" after Shareholding: 12-month dividend per share, yield on the last close (only when both exist, the close is
above zero and no split or bonus went ex in the window), the latest 8 actions, and a reason for every blank. Checked in headless Chromium at 320, 390 and 1280 px,
light and dark: no page overflow, no script errors. Recorded as a decision edit.

**Phase 5b.** `coverage_report.py` gains `corporate_actions` and `share_holdings_upstox`: not measurable without the cache (never zero), and per-stock classification with it.

**Phase 6.** The read-only probe now also requests corporate actions and share holdings (strict cap 150 + 16 per run). Plan: two pilot runs over 20 companies.

## Coverage, before and after (all 2,548 stocks, offline)

| Field | Before | After this branch | Why unchanged |
| --- | --- | --- | --- |
| Directory, valid ISIN and instrument key | 2,548 (100%) | 2,548 (100%) | |
| Daily OHLCV | 15 (0.59%) | 15 (0.59%) | fill needs approved runs (Stage 2) |
| All indicators computable | 14 (0.55%) | 14 (0.55%) | follows price history |
| Annual history | 922 valid, 2 invalid, 1,624 missing (36.2%) | same | fill runs on the existing schedule |
| NSE shareholding | 14 (2,533 outside the collector by design) | same | |
| Sector | 10 (0.39%) | same | |
| Corporate actions, dividends | no source | not measurable (code ready) | needs the pilot, then Stage 3 |
| Upstox share holdings | no source | not measurable (code ready) | needs the pilot, then Stage 3 |
| Ratios, quarterly, annual statements | not measurable (Actions cache only) | same | |

Data coverage cannot rise without the approved requests: this branch makes the pipeline ready and measurable; it adds no data.

## Tests (after the last commit)

Python: 1,853 tests OK (1 skipped). JavaScript: 30 of 30 files pass (2 helper files are not tests). Guard and pin tests pass against `HEAD`.
Mutation checks: the indicator test caught 4 of 4; the corporate-actions page test caught 4 of 4.

## Remaining gaps

| Gap | Stocks | Status |
| --- | --- | --- |
| Daily price history | 2,533 of 2,548 never fetched | Stage 2 (6 runs) after approval and merge |
| Corporate actions, dividends, yield | all | code and page ready; pilot, then Stage 3 |
| Shareholding beyond the NSE collector | 2,534 | Upstox share holdings ready; pilot, then Stage 3; the page still shows the NSE section only |
| Annual history never requested | 1,624 | existing schedule, 14 to 27 days |
| Ratios, latest statements, quarterly | not measurable offline | Actions cache only |
| Sector | 2,538 | profile endpoint; pilot first |
| Debt components, capex, FCF, bank revenue | all | need real statement lines (pilot keeps them) |
| Sector KPIs (NIM, GNPA, CASA, etc.) | all | not in Upstox responses; 20-company filings pilot |
| BSE-only companies | | Phase 5 of the roadmap (ISIN-based identifiers ready) |
