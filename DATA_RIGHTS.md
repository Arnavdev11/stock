# StockLens data rights record

StockLens is free and non-commercial: no advertising, no paid subscriptions or features, no donations. This file records what is known about the right to show
provider data publicly, what it covers and what is still open. Update it when new written evidence arrives; quote the source, do not paraphrase it wider.

## Evidence on record

| # | Source | Date recorded | What it says (as reported) | Status |
|---|---|---|---|---|
| 1 | Ketan, Developer @ Upstox, reply on the Upstox developer forum (community.upstox.com). Reported by the repository owner on 2026-10-28; the thread link is still to be added here. | 2026-10-28 | Live prices and indices may be displayed on a public educational website for informational and educational purposes. Visitors do not need their own Upstox accounts. No separate permission or additional NSE/BSE market-data licence is needed for that stated use through Upstox APIs. Commercial features need a separate business arrangement. Data should be fetched server-side and served to visitors within the applicable limits. | Written statement by an Upstox developer on a public forum. Relevant evidence for the stated use. Not a signed agreement. |
| 2 | Earlier Upstox forum replies (undated in the page, author not shown): [Live data feed for startups](https://community.upstox.com/t/live-data-feed-for-startups-to-show-data-on-their-platform/6142), [API usage clarification](https://community.upstox.com/t/api-usage-clarification/13250) | 2026-10-28 | One says public display of live data for free or commercial use needs distribution rights from NSE (NSE Dotex); another allows distribution "to Upstox users". | Older and narrower; evidence 1 is the more specific statement for this site's use. Kept so the record is complete. |

## Scope

**Covered by evidence 1 (as worded):** live prices and index values shown on this free educational site, fetched server-side through the Upstox API.

**Open scope questions** (not answered explicitly by evidence 1; engineering continues, these are not blockers for unrelated work):

1. Full-universe daily OHLCV history (about 2,500 stocks), stored and republished as static JSON.
2. Stored annual and quarterly financial statements, the financial-history ledger and its revisions.
3. Fundamentals and valuation ratios (P/E, P/B, ROE and similar) as published by Upstox.
4. Corporate actions and shareholding (the shareholding collector reads NSE XBRL filings, not Upstox).
5. Retention: how long stored data may be kept and republished.
6. Index datasets other than live index values (for example the stored NIFTY 50 benchmark candles, and BSE SENSEX data, which stays behind its own closed gate in
   `sensex_publish.py`).
7. NSE end-of-day bhavcopy files downloaded directly from NSE archives (`nse_updater.py`): a separate source with its own terms, not covered by an Upstox statement.

## Rules this repository keeps

* No paid feature, advertising, subscription or commercial redistribution.
* Credentials stay server-side (GitHub secrets, the relay's environment); no token reaches a visitor.
* Provider limits are respected: call budgets per run, pacing, back-off; no uncontrolled parallel requests.
* Attribution: pages name the source of each dataset.
* If a provider or exchange objects, the affected dataset is withdrawn from the site first and the question is resolved after.
