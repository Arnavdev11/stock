# Responsive redesign (English only) — final review package

* **Branch:** `feat/responsive-redesign-rebuild`, based on `main` at `66fe34a589e0149c50fd285029e2a1f20cd8fcf5`. `main` is untouched.
* **Head:** the last commit in the table below; its full SHA is in the final report and inside the bundle's file name.
* **What this is:** a **reconstruction** of the planned responsive work from the agreed specification. It is not the lost branch `feat/responsive-layout-pr1` / `c2658f0`.
* **Not changed:** nothing was pushed, merged, deployed, published or dispatched. No stock name, market data, historical price, calculation, schema, workflow, API behaviour, authentication, rate limit, budget, cache logic or protected-script hash was changed. The chart dependency (URL and pinned version 4.2.0) is unchanged, and no SRI attribute has been added.

**Status: ready for your review, not for release.** Every automated check below passes. Release still needs:
* your own visual review;
* the price chart checked with its library loaded (the attribution line and logo are tested here only with a stand-in library), and SRI added once a verified hash is available (see *Chart library*);
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
| `64368e8` | Round 5: Financial History and shareholding quarters as grid cards on phones; stock-table browser checks |
| `659ef32` | Round 5: screen filters disabled, with a reason, when the fundamentals file is missing; content checks |
| `d9934ee` | Round 5: screener scale checks against an independent oracle (520 synthetic stocks) |
| `a81cca8`, `00dfb56` | Rounds 5 and 6 review packages |
| `fa318be` | **Round 7:** visible TradingView attribution (NOTICE text and link); logo kept on explicitly |
| `4d4ba74` | **Round 7:** two-column stage 1 (heading helpers accept a heading inside a direct-child `.sd-group`) |
| `c603466` | **Round 7:** two-column stage 2 (grouping lines, no layout CSS) |
| last commit | **Round 7:** this review package |

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

## Stock page two-column layout: stages 1 and 2 done; stage 3 not approved

**Round 7, implemented (approved):**
* **Stage 1** (`4d4ba74`): `overviewHeading()` (Investor Snapshot) also accepts a direct-child `.sd-group` whose **first** element is the "Overview" heading. It returns the group, so the snapshot is still inserted directly before it with `insertBefore` on `#detail`. `heading()` (section menu) also accepts a heading whose parent is a direct-child `.sd-group`, and returns the heading itself as the scroll target. On today's flat page both return exactly what they returned before.
* **Stage 2** (`c603466`): `view(s,d)` gains six inserted lines that wrap three existing runs in `<div class="sd-group">`:
  * `#sdRatios`: the Overview heading, its tiles and the fundamentals note;
  * `#sdHealth`: Financial Health with its notes and two tile rows;
  * `#sdCash`: Cash Flow with its note, tiles and free-cash-flow note.
  The Technical Snapshot and the price chart stay outside every group. No line of `view(s,d)` was changed or moved, and **no CSS was added**, so the groups are plain blocks.
* **Guard probe:** the inserted ids and comments are neutral (`sdRatios`, `group ratios open`, …). The first "Overview" in the page is still line 489, outside every recorded edit.
* **Tests:** new `tests/test_detail_groups.js` (64 checks).
  * Stage 1: both helpers on a flat and a grouped `#detail`, including cases that must not match: too deep, a group that doesn't open with the heading, another wrapper, a near-miss class name.
  * Stage 2: exactly three openers and closers on lines of their own, balanced and not nested, distinct ids, the right first heading in each group, chart and Technical Snapshot outside, no grouping line containing the probed word, no layout CSS, and no other line of `view(s,d)` differing from the page with recorded edits reversed.
  * Mutation-checked on scratch copies: a group id or comment containing "Overview", an unclosed group and an added `.sd-group{display:grid}` each fail it. A changed heading line is caught by the existing Stock Detail pins (`test_checklist.js`, `test_research.js`) rather than by this file.
* **What changed visually: nothing.** See the results under *Tests and checks*.

**Earlier investigation (round 5):** the two guard tests failed in experiment 2:

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

## Chart library: attribution done (round 7); SRI not added

**Evidence:**
* The page loads `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` at runtime. It's pinned to 4.2.0, and `tests/test_chart.js` asserts that version.
* There is no `integrity` attribute and no local copy. On failure the page shows "Chart library could not be loaded" with no errors.
* The page uses the v4 API (`createChart`, `addCandlestickSeries`, `addLineSeries`, `addHistogramSeries`). v5 replaced these, so moving to v5 would mean code changes.
* **Licence, verified at the exact version** (round 6): the official repository `tradingview/lightweight-charts`, tag `v4.2.0` (commit `23c42ebeddde8073e7748e0b709ba61360a04849`, 2024-07-26), cloned read-only into a scratch folder.
  * `package.json`: `lightweight-charts` `4.2.0`, licence `Apache-2.0`.
  * `NOTICE`: "TradingView Lightweight Charts™ / Copyright (с) 2023 TradingView, Inc. https://www.tradingview.com/".
  * README at that tag: the licence requires naming TradingView as the product creator. The page users see must carry the attribution notice from `NOTICE` and a link to https://www.tradingview.com/. The `attributionLogo` chart option "will satisfy the link requirement".
  * `src/api/options/layout-options-defaults.ts`: `attributionLogo: true` by default. The logo is a link to tradingview.com titled "Charting by TradingView".
* **Before round 7:** the chart's `layout` options set only `background`, so the default logo (and therefore the link) appeared whenever the chart loaded, but the NOTICE attribution text was not on the page.
* **SRI hash: still not computed (round 7).** The exact pinned file could not be obtained here, and I did not guess a hash:
  * `unpkg.com` and `cdn.jsdelivr.net`: refused by this environment's network policy (proxy `CONNECT` 403). Per the environment's rules, not retried or routed around.
  * `registry.npmjs.org` (the npm tarball): the host does not resolve from here.
  * The GitHub tag's source is not the built `dist` file, so it cannot be hashed from the clone. A page-to-text fetcher does not return exact bytes, so it was not used for hashing.
* **Commands to run on your machine** (either shell). They download from both CDNs and from npm, confirm all three files are byte-identical, and print the SRI value:
  ```bash
  F=lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js
  curl -fsSL "https://unpkg.com/$F" -o lw-unpkg.js
  curl -fsSL "https://cdn.jsdelivr.net/npm/$F" -o lw-jsdelivr.js
  npm pack lightweight-charts@4.2.0 && tar -xzf lightweight-charts-4.2.0.tgz package/dist/lightweight-charts.standalone.production.js
  npm view lightweight-charts@4.2.0 dist.integrity        # the registry's own integrity value for the tarball
  cmp lw-unpkg.js lw-jsdelivr.js && cmp lw-unpkg.js package/dist/lightweight-charts.standalone.production.js && echo "all three identical"
  echo "sha384-$(openssl dgst -sha384 -binary lw-unpkg.js | openssl base64 -A)"
  ```
  ```powershell
  $F = "lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
  Invoke-WebRequest "https://unpkg.com/$F" -OutFile lw-unpkg.js
  Invoke-WebRequest "https://cdn.jsdelivr.net/npm/$F" -OutFile lw-jsdelivr.js
  (Get-FileHash lw-unpkg.js -Algorithm SHA384).Hash -eq (Get-FileHash lw-jsdelivr.js -Algorithm SHA384).Hash   # True = byte-identical
  "sha384-" + [Convert]::ToBase64String([Security.Cryptography.SHA384]::Create().ComputeHash([IO.File]::ReadAllBytes("$PWD\lw-unpkg.js")))
  ```
  Send me the printed `sha384-…` value and the result of the identity check. Only if all copies agree will SRI be added, as a separate approved change.

| | 1. CDN + SRI (keep unpkg, exact version, `integrity` + `crossOrigin="anonymous"`) | 2. Vendor a reviewed copy (with LICENSE and NOTICE) | 3. Another available dependency |
|---|---|---|---|
| Security | The browser refuses any file that doesn't match the hash. Third-party request at runtime. | Best: the reviewed bytes are in version control; no third-party runtime request. | No other chart library is in the repo. The only alternative is the page's own inline-SVG charting (Financial History, Shareholding, Compare), which has no candlesticks, MACD, crosshair or zoom. |
| Reliability | Depends on unpkg being up; with SRI, a changed file fails closed (no chart, page intact). | Same availability as the site. | Rewriting the price chart in SVG: large and risky. |
| Licensing | Attribution notice and link still needed. | Ship LICENSE and NOTICE beside the file; attribution notice and link still needed. | Not applicable. |
| Offline | No chart. | Works. | Works. |
| Maintenance | Update the URL and hash together on each upgrade. | Re-vendor and re-review on each upgrade. | High. |
| Change needed | Two attributes in the chart script (protected: an approved edit), the `test_chart.js` assertion for the integrity value, and a hash computed on a networked machine. | A new file, plus a change to the protected `update.yml` site-build step so the file is copied into `_site` (workflow approval), plus the URL change in the chart script. | A new chart implementation. |
| Impact on the chart | None if the hash is right. | None. | Total rewrite. |

**Round 7: attribution implemented** (`fa318be`, approved):
* One inserted line in the chart's `draw()`, shown under the indicator notes whenever the chart is drawn: "Price, volume and indicator charts: TradingView Lightweight Charts™ · Copyright © 2023 TradingView, Inc. · https://www.tradingview.com/". The address is a real link (`target="_blank" rel="noopener noreferrer"`). The NOTICE file writes the copyright sign as "(с)"; the page shows "©".
* The chart options now set `attributionLogo:true` explicitly (the library default), so the TradingView logo link stays on and is testable.
* If the library fails to load, no TradingView chart is shown, so neither the line nor the logo appears. The existing "Chart library could not be loaded." message is unchanged.
* The library URL and pinned version are unchanged, and no `integrity` or `crossOrigin` attribute was added. `test_chart.js` asserts both: there is no SRI claim, and adding SRI later must update that assertion.
* Tested by `test_chart.js` (+11 checks: the line, its NOTICE text, the exact link, not hidden, exactly once, every chart created with `attributionLogo: true`, the code never turning the logo off, URL unchanged, no SRI attribute).
* **Browser check with a stand-in library:** the real library cannot load here, so a test-only stub was served for its URL. The stub records the chart options and draws a placeholder logo link. Read-only real TCS page, widths 320, 360, 375, 390, 430, 768 and 1280, light and dark: **14 of 14 views pass.** Text contrast is 5.65:1 (light) and 6.64:1 (dark); link contrast is 6.83:1 and 6.30:1; font 12.8px; no horizontal overflow at any width; the logo option is on for all 5 chart panels. The link is an inline text link (about 181×15px), which the WCAG 2.2 target-size rule exempts.
* **Not verified:** the real library's logo rendering, and the attribution with the real chart drawn.

**Next for the chart: SRI (option 1), once a verified hash is available.** Add `integrity="sha384-…"` and `crossOrigin="anonymous"` to the script the chart code creates, and update the `test_chart.js` assertion to the exact value. Then check, where the library loads, that the chart renders and that a wrong hash shows the existing fallback.

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

## Tests and checks (exact commands, actual results on the final code, round 7)

Run after each task (`fa318be`, `4d4ba74`, `c603466`) and again on the final commit; every run gave the results below.

| Command | Result | Baseline (`main` at `66fe34a`) |
|---|---|---|
| `python -m unittest discover -p 'test_*.py'` | **1,811 tests, OK, 1 skipped** | 1,807 tests, OK, 1 skipped (+4 new manifest tests) |
| `for f in tests/test_*.js; do node "$f"; done` | **28 of 28 files pass** | 25 of 25 (+3 new: screener filters 170 checks, responsive layout 57, detail groups 64) |
| The two guard tests, `test_research_test_batch.Isolation.test_index_html_differs_from_the_commit_only_by_the_market_cap_header` and `…test_an_unrelated_change_is_still_detected_after_the_stage_8c_reversal` | **both OK**, after each task; `test_research_test_batch.py` is byte-identical to `66fe34a` and `origin/main` | — |
| `node tests/test_chart.js` | **176 checks pass** (includes the 10 new attribution checks) | — |
| Protected-script checks (inside both suites: byte-identity guards, Stage 8C, Phase 2B pin, chart pins, Stock Detail pins, search pin, script count, manifest) | **all pass**; no hash value changed; no workflow, guard, pin file, updater or data file differs from `66fe34a` | — |
| `browser_checks.py`, 15 widths × light/dark, on stage 2 | **0 problems in 600 checks**: synthetic 90 + 90 (fallback font / DejaVu Sans), real copy 90 + 90 (home, TCS, compare), combined 120 + 120 (home, screener, TCS, compare) | — |
| `browser_content_checks.py` on the real copy / the combined preview | Stage 1: **26 page views, 0 problems** (real copy). Stage 2: **26 + 26, 0 problems** | — |
| Screenshot comparison against `00dfb56` (start of round 7): full-page stock pages, 15 widths × light/dark | Stage 1: **90 of 90 pixel-identical**. Stage 2: **90 of 90 pixel-identical** (real copy TCS and HDFCBANK, combined TCS) | — |
| Grouped-structure probe in the browser (real copy TCS, HDFCBANK, RELIANCE; combined TCS; 390 and 1280px) | **8 of 8 pass**: three `.sd-group` children of `#detail` in order, each opening with its heading; the Investor Snapshot directly before `#sdRatios`; groups computed as plain blocks; the Fundamentals, Financials and Technical section links scroll their target to the top (69px at 1280, 119px at 390, under the sticky bar); Back and Forward return to the right sections | — |
| Attribution check with a stand-in library (real copy TCS, 7 widths × light/dark) | **14 of 14 pass** (see *Chart library*) | — |
| `browser_screener_checks.py` | **39 of 39 pass** | — |
| `browser_screener_scale_checks.py` (520 synthetic stocks) | **29 of 29 pass** | — |
| `browser_mobile_tables_checks.py` (4 tables, 320–430 and 768/1280px, light/dark) | **799 of 799 pass** | — |

**Widths:** 320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440 and 1920.

**Not real-data verification:** the screener, scale, mobile-table and combined-preview results use synthetic or fixture data wherever no real file is in git (see the data table below). The attribution check used a stand-in for the chart library.

**Timing flake found (pre-existing, not changed):** `tests/test_chart.js` waits a fixed 40ms after loading a page before asserting. Under heavy CPU load (load average about 19, while six browser runs were in progress) it failed 9 of 12 times, and the copy at `00dfb56` failed at the same rate. Idle, both passed 20 of 20. Under a synthetic CPU load the current copy failed 3 of 8. It is the same kind of race already fixed in `test_checklist.js`. All results above come from runs on an idle machine. Fixing it (waiting for the chart instead of a fixed time) would be a separate test-only change.

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
* **Hashes:** no hash value was replaced. The Phase 2B pin and the chart-block pins (`ef5a3484…`, in `test_technicals.js`, `test_profiles.js`, `test_checklist.js`, `test_research.js`) keep their original values and are checked after reversing exactly the recorded edits. The Stock Detail, dashboard and search-module pins and the pinned script count (18) are unchanged.
* **Tests changed to describe the new layout:** `test_global_navigation.js` and `test_market_dashboard_ui.js`. `test_checklist.js` got only a timing change (same assertion), with its root cause documented in the previous package.

## Files changed (against `66fe34a`)

| File | Why |
|---|---|
| `index.html` | All the page changes above, as recorded edits |
| `tests/approved_page_edits.json` | The recorded edits (`decision_*` keys only; the coverage edits are untouched) |
| `tests/page_edit_manifest.py`, `test_responsive_redesign.py` | Record and verify the edits |
| `tests/legacy_5i.js` | Adds `withoutDecisionEdits` (same exactly-once rule) for the Phase 2B pin |
| `tests/test_technicals.js` | The same Phase 2B and chart-block hashes, checked after reversing the recorded edits |
| `tests/test_profiles.js`, `tests/test_research.js` | The same chart-block hash, checked on the page with recorded edits reversed (as their other pins already were) |
| `tests/test_chart.js` | TradingView attribution checks (round 7) |
| `tests/test_detail_groups.js` | New: two-column stages 1 and 2 (round 7) |
| `tests/test_global_navigation.js`, `tests/test_market_dashboard_ui.js` | Describe the new breakpoints and the removed home sections |
| `tests/test_checklist.js` | Timing fix for a race in the test itself (same assertion); the same chart-block hash, checked with recorded edits reversed |
| `tests/test_screener_filters.js`, `tests/test_responsive_layout.js` | New regression tests |
| `tests/synthetic_site.py`, `tests/readonly_data_site.py`, `tests/hybrid_preview_site.py` | Local preview sites, written to scratch folders only |
| `tests/browser_checks.py`, `browser_screener_checks.py`, `browser_screener_scale_checks.py`, `browser_mobile_tables_checks.py`, `browser_content_checks.py` | Local browser checks (not run by CI; need Playwright) |
| `RESPONSIVE_REDESIGN.md` | This review package |

## Known defects, risks and unverified areas

* **Chart library:** runtime CDN request without SRI; the hash could not be obtained here. The attribution line and logo are tested only with a stand-in library; the real library's rendering is not verified.
* **Real data gaps:** the screen with data, stock tiles, Research and Checklist fundamentals, home market sections and Quarterly Results were checked only with synthetic or fixture data.
* **Phone page length:** the shareholding history and Financial History are now long card lists (all values visible; nothing scrolls sideways).
* **Sector filter** will offer few sectors with real data until the sector approval gate opens.
* **Browser coverage:** headless Chromium only.
* **Manrope:** not measured.
* **Two-column stock page:** stages 1 and 2 done (no visible change); stage 3 (the desktop layout) not approved and not implemented.
* **`test_chart.js` timing flake** under heavy CPU load (pre-existing; see *Tests and checks*).

## Needs your approval or decision

1. **SRI:** run the commands in *Chart library* on your machine and send the `sha384-…` value with the identity result. Adding it would be a separate approved edit.
2. **Two-column stage 3:** choose a desktop layout (for example: Overview + Cash Flow left, Financial Health right, chart and Technical Snapshot full width), then approve stage 3. Not started.
3. **Optional:** a test-only fix for the `test_chart.js` timing flake.
4. Any release action (push, pull request, merge, deploy).

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
