# Responsive redesign (English only) — final review package

* **Branch:** `feat/responsive-redesign-rebuild`, based on `main` at `66fe34a589e0149c50fd285029e2a1f20cd8fcf5`. `main` is untouched.
* **Head:** the commit that adds this file (see the final report for its full SHA). All 17 commits are listed below.
* **What this is:** a **reconstruction** of the planned responsive work from the agreed specification. It is not the lost branch `feat/responsive-layout-pr1` / `c2658f0`.
* **Not changed:** nothing was pushed, merged, deployed, published or dispatched. No stock name, market data, calculation, schema, workflow, authentication, rate limit, budget, cache logic or protected-script hash was changed.

**Status: ready for your review, not for release.** Every automated check below passes. Release still needs:
* your own visual review;
* the price chart checked with its library;
* real fundamentals, financials, quarterly and market files checked in a browser (only synthetic or fixture versions were available here).

## Commits (oldest first)

| Commit | What |
|---|---|
| `d8d8ad0` | Synthetic preview site and browser checks (tools only) |
| `53890e4` | Single-row top bar, full search placeholder, AA colours, touch targets |
| `95cb4a8` | Filter-first Fundamental screen; All-Time High/Low and Sector Leaders/Laggards removed from the home page |
| `e874658` | Page-edit manifest fix (a recorded new text must not already exist in the base page) |
| `60e065d` | Compact mobile cards, wrapping screen tabs, 24px snapshot links |
| `735b1ec` | Layout regression test and first review notes |
| `f61fe5e` | Readable "Compare with…" link in dark mode |
| `3f26da1` | 320–360px: NIFTY values no longer break; cards keep two number columns |
| `e416be2` | Negative P/B handled like negative P/E; Load more (50 per table) |
| `7ffc7fc` | Fixes found with read-only real data: shareholding cards, toggles, link colour, compare links |
| `84352e8` | Financial History row labels on phones |
| `7e93729` | Read-only data preview includes the committed growth events |
| `e29a4bc` | Earlier review package; `--pages` option for the browser checks |
| `66c01ad` | Font headroom: NIFTY 50 tiles 2×2 under a 340px card; `--font` stress option |
| `d159cfd` | Quarterly Results and Investment Checklist as cards on phones; combined preview builder |
| `00ffece` | Card helper moved into the screen script (the page's script count stays at its pinned 18) |
| `638b4e8` | `test_checklist.js`: removes a timing tie (same assertion; root cause below) |
| this commit | This review package |

## What changed on the page

| Area | Change |
|---|---|
| Top bar | Page and bar up to 1200px wide. The full link row is used only from a 1150px bar, so the links never wrap inside it. The desktop search keeps a 15.5rem basis, so its placeholder is never cut off. Tablet (600–1149px): brand, search and menu on one row. Phone (under 600px): search on its own row. |
| Colours | Gains `#0B7A50` and losses `#C23333` pass WCAG AA on white and on the page background. Dark-mode selected tabs, chips, nav items and buttons use dark text on the accent. Links default to the accent colour. |
| Touch targets | At least 24px everywhere (WCAG 2.5.8); 44px for tabs, selects, buttons, the menu button, the search input, the screen's stock links and "Show more" at compact widths. Shareholding "Source" toggles and comparison-table links are 24px. |
| Home page | All-Time High / Low and Sector Leaders / Laggards sections, and the Trending "All-Time High / Low" tab button, removed. Their renderers, data and the Stage 8C pinned code are untouched. NIFTY 50 performance tiles are 2×2 on cards under 340px (phones up to about 390px), so values never break before their "%", even in a font 16% wider than the fallback. |
| Fundamental screen | Filters first: P/E (min, max, which P/E), P/B (min, max, which P/B), ROE at least (existing `#roe`, choices and 15% default kept), Sector. Count line, Clear filters, `aria-sort`, Load more. All three tabs follow the same filters. |
| Screen tables on phones | Below 640px they are cards: every field labelled, numbers in a two-column grid (kept at 320px), column buttons as sort chips, tabs wrap. |
| Stock page on phones | Below a 600px page, Quarterly Results and Investment Checklist tables are cards, so no column starts off-screen. Quarterly cards list each quarter and QoQ Growth; Checklist cards show Value and Status side by side, with a Note only when there is one. Major holders become cards below 680px. Financial History labels keep 10.5rem. Financial History and the shareholding quarter table are year grids and keep their sideways scroll. |
| Stock page on desktop | Wider page, touch-target and contrast fixes. **Still single-column** (see the stock-page section). |

The stock-page card helper only adds a class and each cell's column name to those two sections' tables. It reads nothing, changes no value, row or module code, and runs at the end of the existing screen script, so the page still has exactly 18 script elements.

## Screener behaviour

* **Strict numbers.** A stored value passes an active numeric filter only if it is a finite JSON number. Missing, blank, "-", text, NaN and infinite values never pass. A typed value must be a plain number of 0 or more (`12`, `12.5`, `.5`). Anything else marks the box invalid, announces the message and shows **no rows** in every tab until it is fixed. A minimum above a maximum is also an error.
* **Negative P/E.** A P/E range and "Positive only" list P/E > 0 only. "Negative only (loss-making)" lists P/E < 0 and disables the range boxes. P/E of exactly 0 is neither.
* **Negative P/B.** The same rule: "Negative only (negative book value)" lists P/B < 0 and disables the P/B range. The help text under each choice explains what a negative value means.
* **Sorting.** Text columns sort A–Z first; numbers high–low first. Missing and text values always sort last. For P/E and P/B, zero and negative values sort after every positive value in both directions. Ties keep the file's order.
* **Sector.** An exact match on the published sector; a stock without one never passes a chosen sector. The note says how many stocks have a sector. With no sector data the filter is disabled and says why. Today the updater publishes `sector` only for the ten test symbols.
* **Load more.** Each screen table shows its first 50 matching rows, a "Showing N of M stocks" line and "Show 50 more". Every row is still drawn, so sorting, row links and the other tabs' code are unchanged; rows past the limit are hidden. A filter change starts again at 50; sorting keeps the number shown. Focus moves to the first newly shown stock. Nothing extra is fetched.

## Stock page two-column layout: technically possible, but not done without your approval

The Stock Detail view (`view(s,d)`) returns one flat list: each `<h3>` (Overview, Financial Health, Cash Flow, Price chart) is a **sibling** of its tiles and notes.

**Experiment 1** (previous audit; throwaway worktree): grouping each heading with its content broke:
* the Investor Snapshot (it disappeared: `stocklens-snapshot` `overviewHeading()` finds the Overview heading only among `#detail`'s direct children);
* the "Fundamentals" and "Financials" links (`stocklens-nav` `heading()` requires `h3.parentNode===box`).

**Experiment 2** (this audit; throwaway worktree, discarded, never on the branch):
* *Changes:* the same grouping, plus `overviewHeading()` and `heading()` also accepting a heading inside a direct-child `.sd-group`, plus a desktop grid of whole groups.
* *Result:* the Investor Snapshot was back, all six stock-page modules placed themselves, the section links scrolled correctly, the mobile layout stayed single-column in source order, all 27 JS test files passed, and the Python suite had 1,808 passes, 1 skip and 2 errors.
* *The two remaining failures* (`test_research_test_batch`): both probe the guards by renaming the first "Overview" in the page and expect a plain difference. With grouping, that text sits inside a recorded edit, so the guard raises an error instead. The change is still caught, but not in the form the test expects.
* *Design problems it exposed:* Overview is much shorter than Financial Health (a large empty gap), Cash Flow sits alone, and the price chart becomes half-width. The chart (`autoSize:true`) could not be checked at either width here, because its library is blocked.

**Why it is not on the branch:**
* It edits three protected scripts: the Stock Detail view, `stocklens-snapshot` and `stocklens-nav`.
* It needs the two guard tests above adjusted.
* It needs layout decisions from you.

**Smallest safe next step, if you approve it:**
1. *Behaviour-neutral prerequisite:* make `overviewHeading()` and `heading()` also accept a heading inside a direct-child `.sd-group`. With today's flat page this changes nothing, which existing tests would show. Record it as approved edits.
2. *Then:*
   * group the sections;
   * decide the pairing (for example Overview with Cash Flow and Financial Health on its own, with the chart full width);
   * adjust the two guard tests to accept the guard's error as detection;
   * check the price chart where its library loads.

## Data verification

Previews are built only in scratch folders; nothing is written to `out/` or `data/` and nothing is fetched.

| Preview | Built by | Contents |
|---|---|---|
| Synthetic | `tests/synthetic_site.py` | 119 invented stocks, every file marked synthetic |
| Read-only real copy | `tests/readonly_data_site.py` | From `origin/stocklens-data` at `6f9e7dc`, already in the clone, read with `git show`. The files the workflow copies into the site; `financial_history.json` and `shareholding.json` built offline by the updaters' own `build_output()` (both pass the updaters' `validate_doc`); the committed `company_profiles.json` and `out/growth_events.json`. |
| Combined | `tests/hybrid_preview_site.py` | The read-only real copy. Plus, **only** where no real file is in git: synthetic fundamentals, financials, scans and FII/DII; and a **fixture** TCS Quarterly Results file, built by `quarterly_results_updater.build_outputs()` from its own test fixture (the verified real response shape, invented numbers). `MANIFEST.json` labels every file. |

| Feature | Status |
|---|---|
| Header price, day change, Market Cap (TCS) | **Real data** |
| Technical Snapshot | **Real data** (1,255 daily candles) |
| Investor Snapshot: Price & Performance, Ownership, Future Growth Evidence | **Real data** |
| Financial History (table, CAGR, 5 SVG charts) | **Real data** (5 fiscal years, built offline from the real ledger) |
| Shareholding Pattern (snapshot, major holders, trend, quarter table) | **Real data** (21 quarters, built offline from the real ledger) |
| Comparison page: prices and Financial History parts | **Real data** |
| Market Today NIFTY 50 card | **Real data** |
| Fundamental screen, all three tabs, filters, Load more, mobile cards | **Synthetic only**: `fundamentals.json` and `financials.json` exist only in the Actions cache |
| Stock page Overview / Financial Health / Cash Flow tiles, Research and Checklist (fundamentals parts), company names | **Synthetic only**, rendered together with the real sections in the combined preview |
| Quarterly Results (table, QoQ Growth, phone cards) | **Fixture only** (real response shape, invented numbers); no quarterly file is in git |
| Home market sections (breadth, movers, sectors, volume, FII/DII, swing) | **Synthetic only**. Deriving them from the 10 real stocks would misstate market-wide figures (the code expects every NSE stock, turnover and delivery). |
| Price chart (TradingView Lightweight Charts 4.2.0) | **Not verified**: the library loads from unpkg, which is blocked here. Only the fallback was seen: "Chart library could not be loaded", with no errors and the rest of the page intact. |
| Manrope font | **Not verified**: Google Fonts is blocked. The fallback here is Inter, measured identical to what the page used. Stress-tested with DejaVu Sans forced on every element (measured 16–18% wider than Inter): all matrices pass. Manrope's own metrics could not be measured. |
| SENSEX tab, live test panel | **Not verified in a browser** (their unit tests pass) |

## Tests and checks (exact commands, actual results on the final code)

| Command | Result |
|---|---|
| `python -m unittest discover -p 'test_*.py'` (the CI command) | 1,811 tests, **OK**, 1 skipped (the same skip as on `main`) |
| `for f in tests/test_*.js; do node "$f"; done` | **27 of 27 files pass** (`test_screener_filters.js` 167 checks, `test_responsive_layout.js` 53 checks) |
| `python tests/browser_checks.py <synthetic> <report> --no-shots` | 90 checks (15 widths × home/screener/TCS × light/dark), **0 with a problem** |
| same with `--font "DejaVu Sans"` | 90 checks, **0 with a problem** |
| `python tests/browser_checks.py <real copy> <report> --no-shots --pages home,tcs,compare` | 90 checks, **0 with a problem** |
| same with `--font "DejaVu Sans"` | 90 checks, **0 with a problem** |
| `python tests/browser_checks.py <combined> <report> --pages home,screener,tcs,compare` | 120 checks, **0 with a problem** (screenshots saved) |
| same with `--no-shots --font "DejaVu Sans"` | 120 checks, **0 with a problem** |
| `python tests/browser_screener_checks.py <new-dir>` | **39 of 39 pass** |

* **What the browser checks look for:**
  * page overflow;
  * top-bar rows and overlap, and search placeholder fit;
  * content spilling out of its card, and card tables wider than their box;
  * clipped control text, and controls hidden off-screen in a sideways strip;
  * short numbers that wrap;
  * targets under 24px, and text below WCAG AA contrast;
  * console errors.
* **Widths:** 320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440 and 1920. The reports also list the tables that scroll sideways, for review.
* **The earlier fixes, re-confirmed on the final code:**
  * NIFTY values and card columns at 320–390px: no wrapped numbers in either font.
  * Negative P/E and P/B, Load more, focus, tabs following the filters, row links and the no-sector state: all 39 screener checks pass.
  * Clickable targets and text wrapping: no target under 24px and no wrapped number in any matrix.

**Intermittent failure, now resolved.** The failure was in `tests/test_checklist.js`, "slow files and the user left…", and also happened on `main`.
* *Root cause:* the fake files arrive at 120ms, and the test left the second stock at exactly 120ms (100 + 20), a timer tie. When the navigation won, the Stock Detail view was never rendered, so no checklist placeholder existed and the check failed, although nothing was painted.
* *Evidence:* a 5ms stay failed 5/5 with no section placed; a 40ms stay passed 5/5, on `66fe34a` and this branch alike.
* *Fix:* a 40ms stay, with the same assertion. It passed 30/30, and 15/15 under CPU load. A copy of the page with the checklist's route guard removed fails this check 3/3, so the test still guards what it should.
* The similar pattern on line 282 waits 700ms before checking, so it can't tie.

## Protections

* **Recorded page edits:** every `index.html` edit is one of the 22 `decision_edits` in `tests/approved_page_edits.json`, generated by `tests/page_edit_manifest.py` from the exact difference with `66fe34a`.
  * The existing guards reverse them first, so every older byte-identity pin still runs and still catches any other change.
  * `test_responsive_redesign.py` keeps the record exact.
* **Hashes:** the Phase 2B pin in `tests/test_technicals.js` keeps its original hash, checked after reversing exactly the recorded edits. No hash was replaced, the search-module pin is untouched, and the page's pinned script count (18) is unchanged.
* **Tests changed to describe the new layout:** `test_global_navigation.js` (breakpoints) and `test_market_dashboard_ui.js` (13 sections, the removed two asserted absent; NIFTY grid step). `test_checklist.js` got only the timing change above.

## Known defects, risks and unverified areas

* **Price chart dependency.** The library is loaded at runtime from unpkg (`lightweight-charts@4.2.0`), with no integrity (SRI) hash and no local copy. If unpkg is down or the file changes, the chart fails or runs unchecked code; the page degrades to its message. Adding SRI or self-hosting changes a protected script, so it needs your approval.
* **Phones: sideways scrolling.** Financial History and the shareholding quarter table still scroll sideways; later years and categories start off-screen. They are year grids, where cards would not read better.
* **Real data gaps.** The screener, stock tiles, Research and Checklist fundamentals, home market sections and Quarterly Results were checked only with synthetic or fixture data.
* **Sector filter** will offer few sectors with real data until the sector approval gate opens.
* **Browser coverage.** Headless Chromium only; no Safari, Firefox or real devices.
* **Manrope.** Not measured; covered only by the wider-font stress test.

## Needs your approval or decision

1. The stock-page two-column work and its smallest safe next step (protected scripts; two guard tests).
2. SRI or self-hosting for the chart library.
3. The 50-row page size for the 500-stock screen.
4. Any release action (push, pull request, merge, deploy).

## How to review locally

1. **Bring the branch in.** In your repo folder (`C:\Users\HP\Downloads\stock-live-test`), PowerShell:
   * `git fetch origin` (updates only remote-tracking references, so `66fe34a` is present);
   * `git fetch <path>\responsive-redesign-rebuild-<sha>.bundle feat/responsive-redesign-rebuild:feat/responsive-redesign-rebuild` (creates the branch without touching your files or untracked data);
   * `git worktree add ..\stock-review feat/responsive-redesign-rebuild`, then `cd ..\stock-review`.
2. **Run the tests:**
   * `python -m unittest discover -p "test_*.py"`
   * `Get-ChildItem tests\test_*.js | ForEach-Object { node $_.FullName }`
3. **Build the previews into a scratch folder outside the repo:**
   * `python tests\synthetic_site.py ..\preview-synthetic`
   * `python tests\readonly_data_site.py ..\preview-real` (needs `origin/stocklens-data` locally; a plain `git fetch origin` brings it)
   * `python tests\hybrid_preview_site.py ..\preview-combined`
4. **Open a preview** (from inside a preview folder):
   * Run `python -m http.server 8000 --bind 127.0.0.1`.
   * Open `http://127.0.0.1:8000/index.html`, then `#screener`, `#stock=TCS` and `#compare=TCS,ITC,INFY`.
   * With your internet connection on, the chart library and Manrope will load. That's the part that couldn't be checked here.
   * Check light and dark (your OS theme) and narrow the window, or use the browser's device toolbar at 320–430px.
5. **Optional browser checks** (need `pip install playwright` and `playwright install chromium`):
   * `python tests\browser_checks.py ..\preview-combined ..\report --pages home,screener,tcs,compare`
   * `python tests\browser_screener_checks.py ..\screener-check`

## NIFTY 500 validation plan (separate; nothing here was run)

1. Work in a scratch directory outside the repository, and never write to `out/` or `data/`.
2. Build the 500-symbol directory with `directory_universe.py` from a saved copy of the NIFTY 500 list. Check the count, duplicates and symbol format with the `test_directory_universe.py` rules.
3. Run the updaters' offline paths with mocked provider responses (`tests/mock_responses.py`), never the live API, and with the request ceilings unchanged.
4. Run `coverage_report.py --state-dir <scratch>` offline for per-dataset coverage.
5. Preview 500 rows locally with the screener and Load more, especially on phones.
6. Only after your review: a production run through the existing approved workflow, which you dispatch yourself.
