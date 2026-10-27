# Responsive redesign (English only) — final review package

* **Branch:** `feat/responsive-redesign-rebuild`, based on `main` at `66fe34a589e0149c50fd285029e2a1f20cd8fcf5`. `main` is untouched.
* **Head:** the last commit in the table below; its full SHA is in the final report and inside the bundle's file name.
* **What this is:** a **reconstruction** of the planned responsive work from the agreed specification. It is not the lost branch `feat/responsive-layout-pr1` / `c2658f0`.
* **Not changed:** nothing was pushed, merged, deployed, published or dispatched. No stock name, market data, historical price, calculation, schema, workflow, API behaviour, authentication, rate limit, budget, cache logic or protected-script hash was changed. The chart dependency is unchanged.

**Status: ready for your review, not for release.** Every automated check below passes. Release still needs:
* your own visual review;
* the price chart checked with its library, and the licence attribution decided (see *Chart library*);
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
| `638b4e8` | `test_checklist.js`: removes a timing tie (same assertion) |
| `8f4152d`, `6e39c78` | Previous review package |
| `64368e8` | **This round:** Financial History and shareholding quarters as grid cards on phones; stock-table browser checks |
| `659ef32` | **This round:** screen filters disabled, with a reason, when the fundamentals file is missing; content checks |
| `d9934ee` | **This round:** screener scale checks against an independent oracle (520 synthetic stocks) |
| last commit | **This round:** this review package |

## What changed on the page

| Area | Change |
|---|---|
| Top bar | Page and bar up to 1200px wide. The full link row is used only from a 1150px bar, so the links never wrap inside it. The desktop search keeps a 15.5rem basis, so its placeholder is never cut off. Tablet (600–1149px): brand, search and menu on one row. Phone (under 600px): search on its own row. |
| Colours | Gains `#0B7A50` and losses `#C23333` pass WCAG AA on white and on the page background. Dark-mode selected tabs, chips, nav items and buttons use dark text on the accent. Links default to the accent colour. |
| Touch targets | At least 24px everywhere (WCAG 2.5.8); 44px for tabs, selects, buttons, the menu button, the search input, the screen's stock links and "Show more" at compact widths. |
| Home page | All-Time High / Low and Sector Leaders / Laggards sections, and the Trending "All-Time High / Low" tab button, removed. Their renderers, data and the Stage 8C pinned code are untouched. NIFTY 50 tiles are 2×2 on cards under 340px. |
| Fundamental screen | Filters first: P/E and P/B (min, max, which sign), ROE at least (existing `#roe`, choices and 15% default kept), Sector. Count line, Clear filters, `aria-sort`, Load more. All three tabs follow the same filters. **New:** with no fundamentals file, every filter is disabled and the count line says "Filters are off: the fundamentals file is not loaded." |
| Screen tables on phones | Below 640px they are cards: every field labelled, numbers in a two-column grid, column buttons as sort chips, tabs wrap. |
| Stock page on phones | **No table scrolls sideways at 320–430px.** List cards: Quarterly Results and Investment Checklist (below a 600px page). Grid cards, **new this round**: Financial History (one card per measure, years in a grid, below a 680px page because the table is 635px wide) and the shareholding quarter table (one card per quarter, categories in a grid, below 600px). Major holders become cards below 680px. |
| Stock page on desktop | Wider page, touch-target and contrast fixes. Tables unchanged. **Still single-column** (see below). |

**How the stock-page cards work.** A small helper at the end of the existing screen script adds a class and each cell's column name (`data-label`) to those four tables. It reads nothing, changes no value, row or module code, and keeps the page at its pinned 18 script elements.

**What the new grid cards keep.** Nothing is dropped:
* dashes for missing values;
* Financial History's per-year source notes;
* shareholding revision notes ("Revised filing…");
* each quarter's Source toggle, which joins its quarter's card via `:has()` (browsers without `:has()` show it as a strip under the card). Its sticky `100cqw` box returns to normal flow inside the card.

The trade-off is length: on a phone the shareholding history is 21 quarter cards (about 5,300px, against 2,200px of sideways-scrolling table before).

## Screener behaviour

* **Strict numbers.** A stored value passes an active numeric filter only if it is a finite JSON number. Missing, blank, "-", text, NaN and infinite values never pass. A typed value must be a plain number of 0 or more; anything else marks the box invalid, announces the message and shows **no rows** in every tab until it is fixed. A minimum above a maximum is also an error.
* **Negative P/E and P/B.** A range or "Positive only" lists values > 0 only. "Negative only (loss-making)" and "Negative only (negative book value)" list values < 0 and disable that range. Zero is neither. When sorting, zero and negative values come after every positive value in both directions.
* **Sorting.** Text A–Z first, numbers high–low first; missing and text values last; ties keep the file's order.
* **Sector.** Exact match; a stock without a sector never passes a chosen sector. The note says how many stocks have one. With no sector data the filter is disabled and says why.
* **Load more.** 50 rows per table, then "Show 50 more" with a "Showing N of M stocks" line. All rows are drawn; rows past the limit are hidden. A filter change starts again at 50; sorting keeps the number shown; focus moves to the first newly shown stock.

**Verified at scale this round:** `tests/browser_screener_scale_checks.py` uses 520 **synthetic** stocks, invented and including negative, missing and text values. It recomputes every expected result in Python from the rules above and compares it with the page:
* the set and order of rows, including rows behind Load more;
* each "Show 50 more" (visible rows stay a prefix of the sorted list; filters, sort and count unchanged);
* no duplicates; count and pager lines;
* the other two tabs; negative P/E and P/B; a P/B range; invalid input; Clear filters;
* keyboard-only use;
* 44px phone controls.

## Stock page two-column layout: deferred (not implemented, structure unchanged)

**The two guard tests that failed in experiment 2** (a throwaway worktree, never on the branch):
* `test_research_test_batch.Isolation.test_index_html_differs_from_the_commit_only_by_the_market_cap_header`
* `test_research_test_batch.Isolation.test_an_unrelated_change_is_still_detected_after_the_stage_8c_reversal`

Both check that the guards still detect an unapproved change, by applying `now.replace("Overview", "Overview2", 1)` and expecting a plain difference. The first "Overview" in the page is the Stock Detail view's heading line, `h+='<h3 …>Overview</h3><div class="tiles">…` (`view(s,d)`, line 488 today). The experiment rewrote that line to open a group, so it became part of a recorded edit. The probe then broke that edit, and the guard raised an error instead of returning a difference. The change was still caught, but not in the form the tests expect.

**Why the current architecture makes it risky:**
* `view(s,d)` returns one flat list, with each `<h3>` a sibling of its content.
* Seven pieces of code depend on that:
  * `stocklens-snapshot` `overviewHeading()` (direct-child "Overview" heading);
  * `stocklens-nav` `heading()` (`h3.parentNode===box`);
  * Stock Research, Investment Checklist, Financial History, Shareholding and Quarterly Results, which each place themselves only while their node's parent is `#detail`, next to a sibling anchor.
* All of them are byte-pinned.
* The chart cannot be checked here.
* The experiment's grid was unbalanced: short Overview beside tall Financial Health, Cash Flow alone, and a half-width chart.

**Smallest safe sequence** — each stage is a separate approved commit, and nothing moves on until its checks pass:

| Stage | Change | Tested by, before the next stage |
|---|---|---|
| 0 | Your approval of the plan and of a layout (for example: Overview + Cash Flow left, Financial Health right, chart and Technical Snapshot full width). | — |
| 1 | **Behaviour-neutral:** `overviewHeading()` and `heading()` also accept a heading inside a direct-child `.sd-group`. Nothing in today's page uses that, so nothing changes. Recorded as approved edits. | Both suites unchanged, including the two guard tests. New unit tests that feed both functions a grouped `#detail`. Browser layout and content suites identical to before. |
| 2 | **Grouping only, no new CSS:** add the group openers and closers as **separate inserted lines** in `view(s,d)`, leaving every heading line byte-identical. The "Overview" probe then hits unrecorded text, so both guard tests pass unchanged and no guard test is weakened. | Both suites. `browser_content_checks.py` (every stock page: snapshot present, all modules placed, no errors). Section links and Back/Forward. Screenshots identical to stage 1 at every width (grouping alone changes no pixel). |
| 3 | **Desktop grid** of whole groups, from 1000px only; mobile stays single-column in source order; chart full width. | Layout matrices (both fonts, both themes), content checks, the mobile-table checks, and your visual review of balance. |
| 4 | **Price chart** in its new width. | Checked where the library loads (your machine), including resize. |

**Stages 1 and 2 proven in a throwaway worktree** (round 6; removed afterwards, never on this branch):
* 30 decision edits recorded. The first "Overview" in the page (line 489 there) is outside every recorded edit.
* **Rule that made it work:** inserted opener and closer lines must not contain the word "Overview", including in ids or comments. A first attempt used `sdOverview` and "Overview open" and the probe hit a recorded edit again. Neutral names (`id="sdRatios"`, comment "ratios open") fixed it.
* `python -m unittest discover -p 'test_*.py'`: **1,811 tests, OK, 1 skipped**. JavaScript: **27 of 27 files pass**.
* Both guard tests pass, and `test_research_test_batch.py` is byte-identical to `main`.
* Content checks: 26 page views, 0 problems (snapshot present, all 6 modules placed, section links correct). Stock-table checks: 799 of 799 pass. Screenshots at 390 and 1280px, light and dark, pixel-identical to the branch (grouping alone changes nothing visible).
* Stage 3 needs a layout decision from you; stage 4 needs a machine where the library loads.

## Chart library: options (assessment only, nothing changed)

**Evidence:**
* The page loads `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` at runtime. It's pinned to 4.2.0, and `tests/test_chart.js` asserts that version.
* There is no `integrity` attribute and no local copy. On failure the page shows "Chart library could not be loaded" with no errors.
* The page uses the v4 API (`createChart`, `addCandlestickSeries`, `addLineSeries`, `addHistogramSeries`). v5 replaced these, so moving to v5 would mean code changes.
* **Licence, verified at the exact version** (round 6): the official repository `tradingview/lightweight-charts`, tag `v4.2.0` (commit `23c42ebeddde8073e7748e0b709ba61360a04849`, 2024-07-26), cloned read-only into a scratch folder.
  * `package.json`: `lightweight-charts` `4.2.0`, licence `Apache-2.0`.
  * `NOTICE`: "TradingView Lightweight Charts™ / Copyright (с) 2023 TradingView, Inc. https://www.tradingview.com/".
  * README at that tag: the licence requires naming TradingView as the product creator. The page users see must carry the attribution notice from `NOTICE` and a link to https://www.tradingview.com/. The `attributionLogo` chart option "will satisfy the link requirement".
  * `src/api/options/layout-options-defaults.ts`: `attributionLogo: true` by default. The logo is a link to tradingview.com titled "Charting by TradingView".
* **What the page does today:** the chart's `layout` options set only `background`, so the default logo (and therefore the link) appears whenever the chart loads. **The NOTICE attribution text is not on the page.** That is the remaining licence gap.
* **SRI hash: not computed.** The exact file could not be obtained here: the npm registry is not reachable from this environment, and the GitHub release/API route was refused for this session. I did not work around either refusal or guess a hash. The tag's source is not the built `dist` file, so it cannot be hashed from the clone. Compute it on a networked machine from two CDNs and check that they agree:
  `curl -s https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js | openssl dgst -sha384 -binary | openssl base64 -A`
  (and the same with `https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js`).

| | 1. CDN + SRI (keep unpkg, exact version, `integrity` + `crossOrigin="anonymous"`) | 2. Vendor a reviewed copy (with LICENSE and NOTICE) | 3. Another available dependency |
|---|---|---|---|
| Security | The browser refuses any file that doesn't match the hash. Third-party request at runtime. | Best: the reviewed bytes are in version control; no third-party runtime request. | No other chart library is in the repo. The only alternative is the page's own inline-SVG charting (Financial History, Shareholding, Compare), which has no candlesticks, MACD, crosshair or zoom. |
| Reliability | Depends on unpkg being up; with SRI, a changed file fails closed (no chart, page intact). | Same availability as the site. | Rewriting the price chart in SVG: large and risky. |
| Licensing | Attribution notice and link still needed. | Ship LICENSE and NOTICE beside the file; attribution notice and link still needed. | Not applicable. |
| Offline | No chart. | Works. | Works. |
| Maintenance | Update the URL and hash together on each upgrade. | Re-vendor and re-review on each upgrade. | High. |
| Change needed | Two attributes in the chart script (protected: an approved edit), the `test_chart.js` assertion for the integrity value, and a hash computed on a networked machine. | A new file, plus a change to the protected `update.yml` site-build step so the file is copied into `_site` (workflow approval), plus the URL change in the chart script. | A new chart implementation. |
| Impact on the chart | None if the hash is right. | None. | Total rewrite. |

**Recommendation: option 1, with the attribution done first.** Nothing was implemented; both parts edit protected chart code and need your approval.
1. **Attribution (can be done now):** one small visible line under the price chart: "Charts: TradingView Lightweight Charts™, Copyright © 2023 TradingView, Inc." with a link to https://www.tradingview.com/. Keep `attributionLogo` at its default (on). Make it one recorded page edit, add a `test_chart.js` assertion that the line and link exist, and add a check that the chart options never set `attributionLogo:false`.
2. **SRI (needs the hash from a networked machine):** add `integrity="sha384-…"` and `crossOrigin="anonymous"` to the script the chart code creates. Update the `test_chart.js` assertion to expect that exact value, then check where the library loads that the chart renders and that a wrong hash shows the existing "Chart library could not be loaded" fallback.

Option 2 (vendoring) remains the stronger long-term choice for offline use; it needs a workflow approval.

## Data verification

Previews are built only in scratch folders; nothing is written to `out/` or `data/` and nothing is fetched.

| Preview | Built by | Contents |
|---|---|---|
| Synthetic | `tests/synthetic_site.py [--stocks N]` | 119 invented stocks by default (520 for the scale checks); every file marked synthetic |
| Read-only real copy | `tests/readonly_data_site.py` | From `origin/stocklens-data` at `6f9e7dc`, already in the clone, read with `git show`. The files the workflow copies into the site; `financial_history.json` and `shareholding.json` built offline by the updaters' own `build_output()` (both pass `validate_doc`); the committed `company_profiles.json` and `out/growth_events.json`. |
| Combined | `tests/hybrid_preview_site.py` | The read-only real copy, plus only where no real file is in git: synthetic fundamentals, financials, scans and FII/DII, and a fixture TCS Quarterly Results file (built by `quarterly_results_updater.build_outputs()` from its own test fixture). `MANIFEST.json` labels every file. The home page shows the synthetic scan date beside the real NIFTY date, which is an artefact of mixing sources. |

**Content checks on the read-only real copy** (`tests/browser_content_checks.py`, all 10 real stocks plus the home, screener and compare pages, at 390 and 1280px): 26 page views, **0 problems** (no page errors, no leaked `undefined`/`NaN`/`null`, no missing link targets, no broken link addresses).
* Every section's empty state is honest and specific: "Fundamentals not loaded yet.", "No quarterly results are on file for this company yet.", "Data unavailable — the validated market-activity file is not available".
* The brand (back to the dashboard) and the desktop menu's Shareholding and Research items are written as `href="#"`. They are scripted controls, verified by clicking: the brand returns to the dashboard; the other two move focus to the stock search.
* One small usability point (pre-existing, unchanged from `main`): those two items show no message saying why the search was focused.

| Feature | Status |
|---|---|
| Header price, day change, Market Cap (all 10 real stocks) | **Real data** |
| Technical Snapshot | **Real data** |
| Investor Snapshot: Price & Performance, Ownership, Future Growth Evidence | **Real data** |
| Financial History (table, CAGR, 5 SVG charts, phone cards) | **Real data** (built offline from the real ledger) |
| Shareholding Pattern (snapshot, major holders, trend, quarter table, phone cards) | **Real data** (built offline from the real ledger) |
| Comparison page: prices and Financial History parts | **Real data** |
| NIFTY 50 tiles | **Real data** |
| Screen with no fundamentals file (filters off, honest message) | **Real data** (the real copy has no fundamentals file) |
| Screen with data: filters, sector filter, all three tabs, Load more, cards, 520-stock scale | **Synthetic only** |
| Stock overview tiles, Research and Checklist fundamentals parts, company names | **Synthetic only** (in the combined preview) |
| Home market sections | **Synthetic only** (their real files exist only in the Actions cache) |
| Quarterly Results | **Fixture only** (real response shape, invented numbers) |
| Price chart | **Not verified**: library blocked here; only the fallback was seen |
| Manrope font | **Not verified**: covered by a stress test with a face 16–18% wider than the fallback |
| SENSEX tab, live test panel | **Not verified in a browser** (unit tests pass) |

## Tests and checks (exact commands, actual results on the final code)

| Command | Result | Baseline (`main` at `66fe34a`) |
|---|---|---|
| `python -m unittest discover -p 'test_*.py'` | **1,811 tests, OK, 1 skipped** | 1,807 tests, OK, 1 skipped (+4 new manifest tests) |
| `for f in tests/test_*.js; do node "$f"; done` | **27 of 27 files pass** | 25 of 25 (+2 new: screener filters 170 checks, responsive layout 57 checks) |
| Protected-script checks (inside both suites: byte-identity guards, Stage 8C, Phase 2B pin, search pin, script count, manifest) | **all pass**; no workflow, guard, pin file, updater or data file differs from `66fe34a` | — |
| `browser_checks.py`, 15 widths × light/dark | **0 problems in 600 checks**: synthetic 90 + 90 (fallback / DejaVu Sans), real copy 90 + 90 (home, TCS, compare), combined 120 + 120 (home, screener, TCS, compare) | not run on `main` this round (first round: 60 of 60 views had a problem) |
| `browser_content_checks.py` on the real copy / the combined preview | **26 + 26 page views, 0 problems** | — |
| `browser_screener_checks.py` | **39 of 39 pass** | — |
| `browser_screener_scale_checks.py` (520 synthetic stocks) | **29 of 29 pass** | — |
| `browser_mobile_tables_checks.py` (4 tables, 320–430 and 768/1280px, light/dark) | **799 of 799 pass** | the same check on the previous head `6e39c78`: 100 of 799 fail |

**Widths:** 320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440 and 1920.

**What could not run:** the price chart with its library, Manrope, Safari, Firefox and real devices.

### Which tests use which data (round 6)

A passing test here does not show the feature works with live production data; no test fetches production data.

| Data | Tests |
|---|---|
| **Committed real files in the repository** (read, never written) | `test_financial_history_official.py`, `test_financial_history_storage.py` (`official_financial_records.json`); `test_stock_directory.py` (`reference_sources.json`); `tests/test_profiles.js`, `test_research.js` (`company_profiles.json`); `tests/test_growth_events.js` (`data/` and `out/growth_events.json`) |
| **Workflow and source text only** (they mention `stocklens-data` or file names in assertions about the workflow or code, and read no market data) | `test_coverage_probe.py`, `test_historical_handoff.py`, `test_history_advance.py`, `test_shareholding_storage.py`, `test_cache_provenance.py`, `test_nifty_refresh.py`; `tests/test_company_search.js`, `test_investor_snapshot.js`, `test_checklist.js`, `test_global_navigation.js` (they name `company_profiles.json` but load inline fixtures) |
| **Inline fixtures, stubs and mocks** | All other unit tests. `test_financial_history.py` uses a temp `DATA_DIR` and removes the provider token; `tests/test_live_public.py` uses `mock.patch.dict` on the environment. Neither makes a network call. |
| **Browser: read-only real copy** (`origin/stocklens-data` at `6f9e7dc`, via `git show`) | `browser_content_checks.py`, `browser_checks.py` (real pages) |
| **Browser: synthetic** | `browser_screener_checks.py`, `browser_screener_scale_checks.py` (520 stocks with a Python oracle) |
| **Browser: combined** (real, plus synthetic and a quarterly fixture where no real file is in git) | `browser_mobile_tables_checks.py`, `browser_checks.py` (combined pages), `browser_content_checks.py` |

**Remaining verification gaps:**
* Real fundamentals, financials, scans, FII/DII and Quarterly Results in a browser.
* The price chart with its library loaded, and the attribution logo.
* The Manrope font.
* Safari, Firefox and real devices.
* The SRI hash.

## Protections

* **Recorded page edits:** every `index.html` edit is one of the `decision_edits` in `tests/approved_page_edits.json`, generated by `tests/page_edit_manifest.py` from the exact difference with `66fe34a`.
  * The existing guards reverse them first, so every older byte-identity pin still runs and still catches any other change.
  * `test_responsive_redesign.py` keeps the record exact.
* **Hashes:** no hash was replaced. The Phase 2B pin keeps its original value (checked after reversing exactly the recorded edits), and the search-module pin and the pinned script count (18) are unchanged.
* **Tests changed to describe the new layout:** `test_global_navigation.js` and `test_market_dashboard_ui.js`. `test_checklist.js` got only a timing change (same assertion), with its root cause documented in the previous package.

## Files changed (against `66fe34a`)

| File | Why |
|---|---|
| `index.html` | All the page changes above, as recorded edits |
| `tests/approved_page_edits.json` | The recorded edits (`decision_*` keys only; the coverage edits are untouched) |
| `tests/page_edit_manifest.py`, `test_responsive_redesign.py` | Record and verify the edits |
| `tests/legacy_5i.js` | Adds `withoutDecisionEdits` (same exactly-once rule) for the Phase 2B pin |
| `tests/test_technicals.js` | The same Phase 2B hash, checked after reversing the recorded edits |
| `tests/test_global_navigation.js`, `tests/test_market_dashboard_ui.js` | Describe the new breakpoints and the removed home sections |
| `tests/test_checklist.js` | Timing fix for a race in the test itself (same assertion) |
| `tests/test_screener_filters.js`, `tests/test_responsive_layout.js` | New regression tests |
| `tests/synthetic_site.py`, `tests/readonly_data_site.py`, `tests/hybrid_preview_site.py` | Local preview sites, written to scratch folders only |
| `tests/browser_checks.py`, `browser_screener_checks.py`, `browser_screener_scale_checks.py`, `browser_mobile_tables_checks.py`, `browser_content_checks.py` | Local browser checks (not run by CI; need Playwright) |
| `RESPONSIVE_REDESIGN.md` | This review package |

## Known defects, risks and unverified areas

* **Chart library:** runtime CDN request without SRI (hash not computable here). The NOTICE attribution text is missing from the page (a licence requirement). The link part is met by the default TradingView logo, but only when the chart loads. Needs your decision.
* **Real data gaps:** the screen with data, stock tiles, Research and Checklist fundamentals, home market sections and Quarterly Results were checked only with synthetic or fixture data.
* **Phone page length:** the shareholding history and Financial History are now long card lists (all values visible; nothing scrolls sideways).
* **Sector filter** will offer few sectors with real data until the sector approval gate opens.
* **Browser coverage:** headless Chromium only.
* **Manrope:** not measured.
* **Two-column stock page:** deferred.

## Needs your approval or decision

1. Chart library: approve step 1 (the visible attribution line, as one recorded edit plus `test_chart.js` assertions). Separately, approve step 2 (SRI) once you supply a hash computed on a networked machine from both CDNs.
2. Two-column stock page: approve stage 1 (heading helpers accept `.sd-group`, behaviour-neutral) and stage 2 (grouping lines). Choose a stage-3 layout later.
3. Any release action (push, pull request, merge, deploy).

## How to review locally (separate worktree)

1. **Bring the branch in.** In your repo folder (`C:\Users\HP\Downloads\stock-live-test`), PowerShell:
   * `git fetch origin` (updates only remote-tracking references, so `66fe34a` and `origin/stocklens-data` are present);
   * `git bundle verify <path>\responsive-redesign-rebuild-<sha>.bundle`;
   * `git fetch <path>\responsive-redesign-rebuild-<sha>.bundle feat/responsive-redesign-rebuild:feat/responsive-redesign-rebuild` (creates the branch without touching your working folder or untracked files);
   * `git worktree add ..\stock-review feat/responsive-redesign-rebuild`, then `cd ..\stock-review`.
2. **Run the tests:**
   * `python -m unittest discover -p "test_*.py"`
   * `Get-ChildItem tests\test_*.js | ForEach-Object { node $_.FullName }`
3. **Build the previews outside the repo:**
   * `python tests\synthetic_site.py ..\preview-synthetic`
   * `python tests\readonly_data_site.py ..\preview-real`
   * `python tests\hybrid_preview_site.py ..\preview-combined`
4. **Open a preview.** From inside a preview folder, run `python -m http.server 8000 --bind 127.0.0.1`. Then open `http://127.0.0.1:8000/index.html` and try `#screener`, `#stock=TCS` and `#compare=TCS,ITC,INFY`. With internet on, the chart library and Manrope load: that's what couldn't be checked here. Try light and dark, and 320–430px in the browser's device toolbar.
5. **Optional browser checks** (`pip install playwright`, then `playwright install chromium`):
   * `python tests\browser_checks.py ..\preview-combined ..\report --pages home,screener,tcs,compare`
   * `python tests\browser_mobile_tables_checks.py ..\tables-check`
   * `python tests\browser_screener_scale_checks.py ..\scale-check`
   * `python tests\browser_content_checks.py ..\preview-real`
6. **To discard the review copy:** `git worktree remove ..\stock-review`.

## NIFTY 500 validation plan (separate; nothing here was run)

1. Work in a scratch directory outside the repository, and never write to `out/` or `data/`.
2. Build the 500-symbol directory with `directory_universe.py` from a saved copy of the NIFTY 500 list. Check the count, duplicates and symbol format with the `test_directory_universe.py` rules.
3. Run the updaters' offline paths with mocked provider responses (`tests/mock_responses.py`), never the live API, and with the request ceilings unchanged.
4. Run `coverage_report.py --state-dir <scratch>` offline for per-dataset coverage.
5. Preview 500 rows locally with the screener and Load more, especially on phones (the 520-stock synthetic scale check already passes).
6. Only after your review: a production run through the existing approved workflow, which you dispatch yourself.
