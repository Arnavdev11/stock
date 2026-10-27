# Responsive redesign (English only) — review package

Branch `feat/responsive-redesign-rebuild`, based on `main` at `66fe34a`. This is a **reconstruction** of the planned responsive
work from the agreed specification, not the lost branch `feat/responsive-layout-pr1` / `c2658f0`. Nothing was pushed, merged,
deployed or dispatched. No stock data, symbol, calculation, schema, workflow, authentication, rate limit, cache logic or
protected-script hash was changed.

**Status: ready for your review, not for release.** The automated checks pass, but your own visual review is still needed, and
several features could not be checked with populated data (see *Data verification*).

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
| this commit | Review package (this file) and `--pages` option for the browser checks |

## What changed on the page

| Area | Change |
|---|---|
| Top bar | Page and bar up to 1200px wide. The full link row is used only from a 1150px bar, so the links never wrap inside it. The desktop search keeps a 15.5rem basis, so its placeholder is never cut off. Tablet (600–1149px): brand, search and menu on one row. Phone: search on its own row. |
| Colours | Gains `#0B7A50` and losses `#C23333` pass WCAG AA on white and on the page background. Dark-mode selected tabs, chips, nav items and buttons use dark text on the accent. Links default to the accent colour (the browser's blue was 1.72:1 in dark mode). |
| Touch targets | At least 24px everywhere (WCAG 2.5.8); 44px for tabs, selects, buttons, the menu button, the search input, the screen's stock links and "Show more" at compact widths. Shareholding "Source" toggles and comparison-table links were raised to 24px. |
| Home page | All-Time High / Low and Sector Leaders / Laggards sections, and the Trending "All-Time High / Low" tab button, removed. Their renderers, data and the Stage 8C pinned code are untouched. NIFTY 50 performance values no longer break before "%" on 320–360px phones. |
| Fundamental screen | Filters first: P/E (min, max, which P/E), P/B (min, max, which P/B), ROE at least (existing `#roe`, choices and 15% default kept), Sector. Count line, Clear filters, `aria-sort`, Load more. All three tabs follow the same filters. |
| Mobile tables | Below 640px the screen tables are cards: every field labelled, numbers in a two-column grid (kept at 320px), column buttons as sort chips, tabs wrap. |
| Stock page | Wider page, touch-target and contrast fixes, major-holders cards below 680px (long share counts no longer break), Financial History row labels kept at 10.5rem on phones. **No two-column layout** (see below). |

## Screener behaviour

* **Strict numbers.** A stored value passes an active numeric filter only if it is a finite JSON number; missing, blank, "-", text
  and non-finite values never pass. A typed value must be a plain number of 0 or more (`12`, `12.5`, `.5`). Anything else marks the
  box invalid, announces the message, and shows **no rows**, in every tab, until it is fixed. A minimum above a maximum is also an error.
* **Negative P/E.** A P/E range and "Positive only" list P/E > 0 only, so a loss is never read as a cheap valuation.
  "Negative only (loss-making)" lists P/E < 0 and disables the range boxes. P/E of exactly 0 is neither.
* **Negative P/B** (your decision). The same rule: a P/B range and "Positive only" list P/B > 0 only; "Negative only (negative book
  value)" lists P/B < 0 and disables the P/B range. The help text under each choice explains what a negative value means.
* **Sorting.** Text columns sort A–Z first; numbers high–low first. Missing and text values always sort last. For P/E and P/B, zero and
  negative values sort after every positive value in both directions, so the lowest positive value comes first when sorting
  low–high. Ties keep the file's order.
* **Sector.** An exact match on the published sector; a stock without one never passes a chosen sector. The note says how many
  stocks have a sector. With no sector data the filter is disabled and says why. The updater currently publishes `sector` only for
  the ten test symbols, so with real data this filter will offer few sectors.
* **Load more.** Each screen table (all three tabs) shows its first 50 matching rows, a "Showing N of M stocks" status line and a
  "Show 50 more" button. Every row is still drawn, so sorting, row links and the other tabs' code are unchanged; rows past the limit
  are hidden. A filter change starts again at 50; sorting keeps the number shown. Focus moves to the first newly shown stock.
  Nothing extra is fetched.

## Stock page two-column layout: not safe, structure left unchanged

The Stock Detail view (`view(s,d)`, the "Phase 3 Step 1" script) returns one flat HTML string. Each heading (`<h3>Overview`,
`Financial Health`, `Cash Flow`, `Price chart`) is a **sibling** of its tiles and notes, not their parent. These depend on that:

| Code | Dependency |
|---|---|
| `stocklens-snapshot` `overviewHeading(box)` | finds the Overview `<h3>` only among `#detail`'s direct children, then `box.insertBefore(el, ov)` |
| `stocklens-nav` `heading(box, prefix)` | section links "Fundamentals" and "Financials" find their heading only as a direct child (`h[i].parentNode===box`) |
| Stock Research, Investment Checklist, Financial History, Shareholding, Quarterly Results | each places itself only while its node's parent is `#detail` (`node.parentNode===box`) and inserts next to a sibling anchor (`r.nextSibling`, `fh.nextSibling`, the `<h2>`) |

**Experiment (throwaway worktree, discarded, never committed).** Grouping each heading with its content, the minimal first step of
any genuine two-column layout, caused the following:
* The Investor Snapshot disappeared entirely.
* The "Fundamentals" and "Financials" section links stopped scrolling.
* 13 Python tests failed or errored, and 16 JS test files failed (byte pins of the Stock Detail view, plus the snapshot and navigation behaviour tests).

CSS-only alternatives were rejected. A grid on the flat list would separate headings from their content, which you ruled out. Multi-column layout re-balances as each module inserts its section, so headings can break away from their content. The chart itself is not a blocker: it uses `autoSize:true`.

**Minimum architectural change (needs your approval, because it edits protected scripts):**
1. The Stock Detail view wraps each heading and its content in `<section class="sd-group">`.
2. `overviewHeading()`, `heading()` and the five placement checks accept a heading or anchor inside a direct-child group.
3. A CSS grid on `#detail` places whole groups: two columns on desktop, two narrower on tablet, source order on mobile.
4. Each edit is recorded as an approved page edit, the snapshot, navigation and placement tests are extended, and section links, Back/Forward and the price chart are checked in a browser.

## Data verification

Two data sources were used, both only as local copies in a scratch folder:
* **Synthetic:** `tests/synthetic_site.py`, with 119 invented stocks, all marked synthetic.
* **Read-only real copy:** `tests/readonly_data_site.py`, built from `origin/stocklens-data` at `6f9e7dc`, which was already in the clone (no fetch).
  * The files the workflow copies into the site are used as-is.
  * `financial_history.json` and `shareholding.json` are built offline with the updaters' own `build_output()`. Both pass the updaters' own `validate_doc`.
  * The committed `company_profiles.json` and `out/growth_events.json` are included.

| Feature | Status |
|---|---|
| Header price, day change, Market Cap (TCS) | **Verified with populated data** (real history and shareholding) |
| Technical Snapshot | **Verified with populated data** (1,255 real daily candles) |
| Investor Snapshot: Price & Performance, Ownership, Future Growth Evidence | **Verified with populated data** |
| Financial History (table, CAGR, 5 SVG charts) | **Verified with populated data** (5 fiscal years, built offline from the real ledger) |
| Shareholding Pattern (snapshot, major holders, trend, quarterly table) | **Verified with populated data** (21 quarters, built offline from the real ledger) |
| Comparison page: prices and Financial History parts | **Verified with populated data** |
| Market Today NIFTY 50 card | **Verified with populated data** (real benchmark history) |
| Fundamental screen, all three tabs, filters, Load more, mobile cards | **Verified only with synthetic data** (`fundamentals.json` and `financials.json` exist only in the Actions cache) |
| Stock page Overview / Financial Health / Cash Flow tiles, Research, Checklist (fundamentals parts), company names | **Verified only with synthetic data** (same reason) |
| Home market sections (breadth, movers, sectors, volume, FII/DII, swing) | **Verified only with synthetic data** (scan and market files exist only in the Actions cache) |
| Price chart (TradingView Lightweight Charts) | **Not verified:** the library loads from unpkg, which this environment blocks, so only the "could not be loaded" fallback was seen |
| Quarterly Results | **Not verified:** no quarterly files are in git |
| SENSEX tab, live test panel | **Not verified in a browser** (their unit tests ran and pass) |
| Manrope font | **Not verified:** Google Fonts is blocked; the checks used Inter, which has similar metrics |

## Tests and checks (exact commands, actual results)

| Command | Result |
|---|---|
| `python -m unittest discover -p 'test_*.py'` (the CI command) | 1,811 tests, **OK**, 1 skipped (the same skip as on `main`) |
| `for f in tests/test_*.js; do node "$f"; done` | **27 of 27 files pass** (includes the new `test_screener_filters.js`, 167 checks, and `test_responsive_layout.js`, 46 checks) |
| `python tests/synthetic_site.py <dir>` then `python tests/browser_checks.py <dir> <report>` | 15 widths × home/screener/TCS × light/dark = **90 checks, 0 with a problem** |
| `python tests/readonly_data_site.py <dir>` then `python tests/browser_checks.py <dir> <report> --pages home,tcs,compare` | **90 checks, 0 with a problem** |
| `python tests/browser_screener_checks.py <new-dir>` | **39 of 39 pass** (filters, Load more, focus, tabs, row links, 390px cards, no-sector state) |

* **What the browser checks look for:** page overflow, top-bar rows and overlap, search placeholder fit, content spilling out of its card, clipped control text, controls hidden off-screen in a sideways strip, short numbers that wrap, targets under 24px, text below WCAG AA contrast, and console errors.
* **Widths:** 320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440 and 1920.
* **Intermittent failure:** `tests/test_checklist.js` failed once in one full run ("slow files and the user left…"). It is a timing race in the test (120ms fake file delay against 20ms navigations). The three script blocks it runs are byte-identical to `main`, and it then passed 5/5 serially and 12/12 under CPU load, on both this branch and `main`.

## Protections

* **Recorded page edits:** every `index.html` edit is one of the 22 `decision_edits` in `tests/approved_page_edits.json`, generated by `tests/page_edit_manifest.py` from the exact difference with `66fe34a`.
  * The existing guards reverse them first, so every older byte-identity pin still runs and still catches any other change.
  * `test_responsive_redesign.py` keeps the record exact.
* **Hashes:** the Phase 2B pin in `tests/test_technicals.js` keeps its original hash, now checked after reversing exactly the recorded edits. No hash was replaced, and the search-module pin is untouched.
* **Layout spec tests updated:** `test_global_navigation.js` (new breakpoints) and `test_market_dashboard_ui.js` (13 sections, the removed two asserted absent; NIFTY grid breakpoint).

## Files changed

| File | Why |
|---|---|
| `index.html` | The page changes above (22 recorded edits) |
| `tests/approved_page_edits.json` | The recorded edits (`decision_*` keys only; the coverage edits are untouched) |
| `tests/page_edit_manifest.py`, `test_responsive_redesign.py` | Record and verify the edits |
| `tests/legacy_5i.js` | Adds `withoutDecisionEdits` (same exactly-once rule) for the Phase 2B pin |
| `tests/test_technicals.js` | The same Phase 2B hash, checked after reversing the recorded edits |
| `tests/test_global_navigation.js`, `tests/test_market_dashboard_ui.js` | Describe the new breakpoints and the removed home sections |
| `tests/test_screener_filters.js`, `tests/test_responsive_layout.js` | New regression tests |
| `tests/synthetic_site.py`, `tests/readonly_data_site.py` | Local preview sites (synthetic; read-only real copy), scratch folders only |
| `tests/browser_checks.py`, `tests/browser_screener_checks.py` | Local browser checks (not run by CI; need Playwright) |
| `RESPONSIVE_REDESIGN.md` | This review package |

## Needs your approval or decision

1. Restructuring the stock page for a two-column layout (protected scripts; see above).
2. Any release action (push, PR, merge, deploy).
3. Whether the 50-row page size suits you for the 500-stock screen.

## Remaining work before release

* Your visual review, ideally on a real phone and on Safari and Firefox; only headless Chromium was used.
* A populated-data check of the screener, stock tiles, Research, Checklist and home market sections, using the deployed `fundamentals.json`, `financials.json` and market files.
* Price chart and Quarterly Results checks wherever the chart library and quarterly files are available.
* A final check that the Manrope font fits the bar at 1150–1279px. The search box has headroom for this, but it hasn't been measured.

## NIFTY 500 validation plan (separate; nothing here was run)

1. Work in a scratch directory outside the repository, and never write to `out/` or `data/`.
2. Build the 500-symbol directory with `directory_universe.py` from a saved copy of the NIFTY 500 list. Check the count, duplicates and symbol format with the `test_directory_universe.py` rules.
3. Run the updaters' offline paths with mocked provider responses (`tests/mock_responses.py`), never the live API, and with the request ceilings unchanged.
4. Run `coverage_report.py --state-dir <scratch>` offline for per-dataset coverage.
5. Preview 500 rows locally with the screener and Load more, especially on phones.
6. Only after your review: a production run through the existing approved workflow, which you dispatch yourself.
