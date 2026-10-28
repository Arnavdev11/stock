# StockLens home page: review fixes and restructure

Branch `feat/nse-eod-coverage`. Not merged, not published. Data, workflows and data collection are unchanged.

## Where every removed or moved block went (item 13)

| Before (section) | Its unique information | Now |
| --- | --- | --- |
| Top Gainers / Top Losers | gainers and losers: price, change, volume vs average | **Movers > Gainers**, **Movers > Losers** (same rows, columns, limit of 10) |
| Trending Stocks > Price Movers | gainers and losers merged by size of move | covered by Gainers and Losers (no data of its own) |
| Trending Stocks > Volume Shockers | volume, 20-day average, multiple, change | **Movers > Volume > Price up** |
| Trending Stocks > 52W High / Low | new highs; validated new lows when available | **Movers > 52-week** |
| Trending Stocks > Most Active | turnover and quantity rankings (validated file) | **Movers > Most active**, shown when that data exists |
| New 52-Week High / Low | new highs with previous high; validated lows | **Movers > 52-week**: the New 52-week low card is always shown, with its status when the lows are not available |
| Volume Shockers | as above | **Movers > Volume > Price up** (rule in "About these figures") |
| High Volume + High Gain | volume 2x or more and a gain of 3% or more | **Movers > Volume > Up 3% or more** |
| High Volume + Top Losers | losers with volume 2x or more | **Movers > Volume > Price down** |
| Swing Market Watch > 1D | repeated Gainers, Losers, 52-week highs, volume | in **Movers**; Swing keeps 1W, 30D and 90D, and says where 1D went |
| Swing Market Watch > Relative outperformers | repeated the Relative section | **Relative Outperformance / Underperformance** (same window tabs) |
| Swing > Near 52-week high / low (empty) | none (no data) | named in the **Not yet available** note |
| Breadth tiles: Advances, Declines, Unchanged | already on the breadth bar | the bar |
| Breadth tiles: 52-week lows (empty) | none (no data) | the tile returns when the validated data exists; the status is in the New 52-week low card |
| Breadth tiles: Upper / Lower circuits (empty) | none (no data source exists) | removed: not drawn and not listed in the note; the tiles return by themselves when the scan's breadth carries `upper_circuits` / `lower_circuits` counts |
| Sector Dashboard (empty) | none (no approved file) | hidden; named in the note; returns when published |

Navigation is unchanged: Overview, Fundamentals, Financials and Technical (Swing Market Watch) still land on the same sections. The Trending and
New 52-Week High / Low renderers are kept in the module, off the page, because the approved Stage 8C edit lives in them (as for All-Time High / Low).

## Fixes

1. **New 52-Week Low message.** A data file built during market hours holds the previous day's complete close (the updater and its validator say so), but the page
   called it stale. Now only a source that is not the latest trading day is stale. Reasons use plain words; no file names or internal terms.
2. **P/E, P/B, EV/EBITDA of exactly 0** show as a dash on the home page and in the Fundamental screen, and sort with missing values (0 cannot be a real ratio to a price or an
   enterprise value). A real 0 ROE, ROCE or ROA is kept.
3. **ROE over negative equity** (a negative P/B, or a loss with a positive ROE) is left out of the ROE filter, sorted after every comparable ROE and flagged. Detected from the
   published values themselves. Checked against the real rows from the live screen (SHIVACEM 571.79%, MCCHRLS-B 568.9%, GLOBALVECT, VIVIDHA, SHIVAMAUTO, PRAXIS, OMAXE,
   IDEA, NIBL). The default ROE >= 15 list now starts with ROML and COLPAL.
4. **Swing Market Watch**: symbols never break across lines; one column on a phone, two on a tablet, three only where three fit.
5. **Sector column** hidden while fewer than half the stocks have a published sector (10 of 2,353 live). Nothing is filled in.
6. **Screen numbers right-aligned**; symbols styled like the other tables (bold accent, underline on hover).
7. **Dates** read `27 Oct 2026`; **negative numbers** use a true minus sign (U+2212), on the home page, in the Fundamental screen and its tabs, and in Dividends.
8. **Market Breadth**: the duplicate Advances / Declines / Unchanged tiles are gone; empty tiles are gone.
9. **What's Happening**: 8 representative events (taken in turn from each kind), green with an up arrow for a rise, red with a down arrow for a fall, and a
   real "Show all N events" button (keyboard accessible, `aria-expanded`).
10. **"About these figures"** disclosures hold the calculation rules; dates, sources and "not live" stay visible.
11. **Movers**: one tabbed section (Gainers, Losers, Volume, 52-week, Most active when available).
12. **One "Not yet available" note** lists the blocks without data and without a card of their own (Sector dashboard, Most active, stocks near
    their 52-week high or low), with reasons behind "Why". Upper and lower circuits are not listed; the 52-week low status is shown in its own card.
13. **Market Breadth tiles** wrap and grow, so a tile alone on a row fills it: no empty cell at any width.

## Not changed, and why

* **The Stock Detail page** keeps hyphen minus signs, ISO dates and a P/E of 0 shown as `0.00`. Its modules are protected by byte-identity guards
  (`tests/test_detail_groups.js`, `test_financial_history_charts.js`, `test_financial_history_compare_charts.js`, and the Technical Snapshot hash in four tests).
  Changing them means updating those guards, which needs your approval.
* The home page's off-page Stage 8C renderers keep their approved wording; visitors never see them.

## Tests

* New: `tests/test_homepage_fixes.js` (Fundamental screen, 64 checks, with the real rows), `tests/browser_homepage_checks.py` (13 widths, light and dark).
* Updated where the behaviour deliberately changed: `test_market_dashboard_ui.js` (now 1,059 checks), `test_screener_filters.js`, `test_sector_ui.js`,
  `test_ui.js`, `test_corporate_actions_ui.js`.
* Page edits re-recorded with `tests/page_edit_manifest.py --write`. Stage 8C blocks still match their pinned hashes.
