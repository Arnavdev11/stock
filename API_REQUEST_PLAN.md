# StockLens: consolidated Upstox request plan (awaiting approval)

Branch `feat/nse-eod-coverage`. No request in this plan has been made. Nothing here buys, subscribes to or commits to a provider.
Every request uses the existing read-only Analytics token (GitHub secret `UPSTOX_ANALYTICS_TOKEN`, Actions only; never printed or stored).
No extra permission or scope is needed: all endpoints below are GET requests that the current workflows already make or that the same token serves.

## Licensing caveat (read first)

Upstox staff confirmed the data is for personal use. Publishing it on a public website may not be covered. This plan asks only for a pilot whose
output is a private Actions artifact (not published). Whether the full rollout's data may be shown on the live site is a decision for you, not a technical one.

## Stage 1: pilot (the only thing this approval would run)

Tool: the existing manual workflow "Coverage probe (read-only)", extended on this branch (`coverage_probe.py`, strict cap 150 fundamentals + 16 candle requests per run,
retries included). It writes only the `probe_output` artifact: a report and the raw responses. It reads no cache and writes no data, ledger, `out/` or site file.

| Run | Symbols (PROBE_SYMBOLS) | Why |
| --- | --- | --- |
| A | HDFCBANK, FEDERALBNK, AUBANK, BAJFINANCE, CHOLAFIN, HDFCLIFE, ICICIGI, TCS, PERSISTENT, SUNPHARMA | 3 banks, 2 NBFCs, 2 insurers (financial statements have no plain "Revenue" line), 3 non-financials |
| B | AUROPHARMA, APOLLOHOSP, MAXHEALTH, ITC, HINDUNILVR, ULTRACEMCO, AMBUJACEM, LT, BEL, 20MICRONS | 10 non-financials: pharma, hospitals, FMCG, cement, capital goods, defence PSU, a micro cap |

All 20 are in the published directory with valid ISIN check digits. HDFCBANK and ITC are also two of the 8 stocks whose saved shareholding ISIN differs
from the directory (same issuer), so the pilot shows which ISIN Upstox serves.

Per stock, 9 endpoints (consolidated basis first, standalone only when consolidated is empty):

| # | Endpoint | Fields expected | Gap it resolves |
| --- | --- | --- | --- |
| 1 | `GET /v2/fundamentals/{ISIN}/profile` | sector | sector for 2,538 stocks without one |
| 2 | `GET /v2/fundamentals/{ISIN}/key-ratios` | P/E, P/B, ROE, ROCE, ROA, EV/EBITDA (company and sector values) | ratios (Actions-cache only today, not measurable offline) |
| 3 | `GET .../income-statement?type=consolidated&time_period=yearly&fs=true` | revenue, profit after tax, line items | annual statements; bank/NBFC revenue mapping |
| 4 | `GET .../balance-sheet?type=consolidated&fs=true` | total assets, liabilities, borrowings lines | balance sheet, debt components |
| 5 | `GET .../cash-flow?type=consolidated&fs=true` | operating, investing, financing, capex lines | cash flow, capex, free cash flow |
| 6 | `GET .../income-statement?type=consolidated&time_period=quarterly&fs=true` | revenue, operating profit, PAT by quarter | quarterly results |
| 7 | `GET /v2/historical-candle/NSE_EQ\|{ISIN}/day/{to}/{from}` (180 days; the URL `historical_updater.py` already uses) | daily OHLCV | confirms the candle key form `NSE_EQ\|<ISIN>` before the price fill |
| 8 | `GET /v2/fundamentals/{ISIN}/corporate-actions` | type, ex-date, amount, ratio, record date | dividends, 12-month DPS, yield (no source today) |
| 9 | `GET /v2/fundamentals/{ISIN}/share-holdings` | promoters, FII, other DII, MF, retail % by quarter | shareholding for 2,534 stocks outside the NSE collector |

Requests: 90 per run when consolidated data exists, at most 130 if every statement needs the standalone retry; hard stop at 166 per run.
Both runs: expected 180 to 260, never more than 332. Pacing 2 s per request plus 10 to 30 s back-off on HTTP 429/5xx: about 4 to 6 minutes per run.
Rate limits for the Fundamentals API are not published; the pacing (at most 30 requests a minute) is the same as today's scheduled updaters.

Pass criteria (decided before looking at results):
* endpoints 8 and 9 parse in the documented shape (status `ok`, `partial` or `no_data`, never `rejected`) for at least 18 of 20;
* candles return for all 20 with the `NSE_EQ|<ISIN>` key;
* for each bank, NBFC and insurer the raw income statement and balance sheet are kept, so the revenue and debt line mapping can be written from real lines.

If the pilot fails or stops: nothing is written anywhere except the artifact, so there is nothing to undo. A 401/403 stops the run immediately (token problem).
A failed endpoint is recorded per stock and the run continues. Resume = re-run the probe with only the failed symbols in PROBE_SYMBOLS. Nothing is retried automatically.

What Stage 1 needs from you: permission to push `feat/nse-eod-coverage` (no pull request, no merge) and to dispatch the probe workflow on that branch twice.

## Stages 2 to 4: the rollout (listed so the numbers are known; each needs the merge and a separate go-ahead after you review the pilot)

| Stage | What | Requests | Runs and time | Storage |
| --- | --- | --- | --- | --- |
| 2 | Price-history fill: 2,533 stocks never fetched, 5 years each | 15,198 (6 per stock) | 6 manual runs of the Historical workflow at the 2,699-request ceiling, about 90 min each | about 301 MB of candle shards (94.3 B per candle measured) |
| 2b | Daily price refresh once filled | about 2,833 a day | inside the scheduled run (budget sized by `refresh_budget`, ceiling 3,000): about 95 min a day | +about 0.24 MB a day |
| 3 | Corporate actions and Upstox share holdings, first fill | 5,096 (2 per stock) | 4 runs of about 1,300 requests, about 65 min each (`EXTRA_MAX_CALLS`) | 23 to 41 MB of per-stock files, the same again in the cache (estimate; the pilot gives the real size) |
| 3b | Refresh (14 days for actions, 30 for holdings) | about 270 a day | about 12 min a day | small |
| 4 | Annual history for 1,624 stocks never requested | 3,248 to 6,496 (2 to 4 per stock) | existing schedule at `HISTORY_MAX_CALLS` 120 per run: about 14 to 27 days; faster only with a raised budget | already measured by the existing ledger |

Every stage resumes safely: each updater keeps a per-stock checkpoint (fetched / attempted / failures), retries with a bounded back-off (1, 2, 4, 8, 16, then every 30 days),
never re-downloads verified history (only the missing tail is requested), stops at its call budget and continues from the next unreached stock on the next run.
A failure never deletes data saved earlier, and every stock ends with a recorded status.

Stage 3 also needs `fundamentals_extra_updater.py` wired into a workflow (not done; a test asserts no workflow calls it) and its two outputs added to the publish step.
