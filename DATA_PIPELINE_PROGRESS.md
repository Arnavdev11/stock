# StockLens EOD data pipeline: progress report

Branch `feat/nse-eod-coverage` (from `main` at `92c0fe0`). Local only: nothing pushed, merged, deployed or refreshed. No external API call has been made.
Baseline data: `stocklens-data` at `19c7c07` (27 Oct 2026 16:19 UTC), measured offline with `coverage_report.py --as-of 2026-10-27`.

## Phases

| Phase | Status | Commit |
| --- | --- | --- |
| 0. Indicator readiness measured from the page's own minimums | Done | `049ecf6` |
| 1. Directory audit: symbols, ISIN check digits, instrument keys, cross-file identity | Done | this commit |
| 2. Price history: scheduled refresh budget, storage, backfill plan | Next | |
| 3. Fundamentals fixes in the existing updaters | Planned | |
| 4. New Upstox endpoints (corporate actions, share-holdings) offline | Planned | |
| 5. Frontend integration | Planned | |
| 6. Consolidated API request plan (one approval) | Planned | |
| 7. Final validation and report | Planned | |

## Phase 1: directory audit (result on real data)

* 2,548 directory stocks; 2,548 valid: well-formed symbol and ISIN, ISO 6166 check digit correct, matching the published directory.
* Instrument key `NSE_EQ|<ISIN>` derivable for all 2,548. Whether Upstox still lists each is known only when a request is made.
* 8 saved records carry an ISIN that differs from the directory's (6 shareholding, 2 annual history). All 8 share the issuer code with the directory's ISIN:
  another or earlier security of the same company, not a different company. They are reported, never remapped.
  Shareholding: 360ONE, HDFCBANK, ICICIBANK, INFY, ITC, SBIN. Annual history: BANSALWIRE, CCAVENUE.
* Tests: `coverage_report.isin_check_digit_ok`, `same_issuer_code`, the invalid reason `isin_check_digit_wrong`. The test fixtures now generate ISINs
  with correct check digits, as every real ISIN has.

## Remaining gaps (updated per phase)

| Gap | Stocks | Status |
| --- | --- | --- |
| Daily price history | 2,533 of 2,548 never fetched | Fill needs approved runs (Phase 6 plan) |
| Scheduled price refresh capped at 150 calls | all once filled | Phase 2 |
| Annual history never requested | 1,621 | Budget; Phase 3 |
| Ratios, latest statements, quarterly results | not measurable offline | Actions cache only |
| Dividends, corporate actions | all | Phase 4 (endpoint never called) |
| Shareholding percentages | 2,534 | Phase 4 |
| Debt, capex, free cash flow, sector KPIs | all | Not in Upstox responses; 20-company pilot |
