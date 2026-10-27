// Run: node tests/test_market_dashboard_ui.js   (no network, no browser; the module's pure functions are evaluated against small fixtures)
// Tests Phase 5J: the Market Today dashboard that replaces the descriptive homepage. Data-only; every value shown comes from fixtures the test builds itself.
const fs = require("fs"), cp = require("child_process"), assert = require("assert");
const { legacy } = require("./legacy_5i.js");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const near = (a, b, m) => { checks++; assert.ok(Math.abs(a - b) < 1e-9, m + " (" + a + " vs " + b + ")"); };
const modOf = (h, tag) => (h.split(tag)[1] || "").split("</script>")[0];
const MTAG = '<script type="module" id="stocklens-market">';
const code = modOf(html, MTAG), css = (html.match(/<style id="marketCss">([\s\S]*?)<\/style>/) || [, ""])[1];
ok(code.length > 5000 && css.length > 1000, "the market module and its styles are found");
const main = (html.match(/<main id="market">[\s\S]*?<\/main>/) || [""])[0];
ok(main.length > 500, "the dashboard <main id=\"market\"> exists");

// ---------- 1. structure: every section, in order, with its heading ----------
const SECTIONS = [["mk-today", "Market Today"], ["mk-breadth", "Market Breadth"], ["mk-happening", "What's Happening"], ["mk-sectors", "Sector Dashboard"], ["mk-movers", "Top Gainers / Top Losers"], ["mk-trending", "Trending Stocks"], ["mk-52w", "New 52-Week High / Low"], ["mk-volume", "Volume Shockers"], ["mk-hvgain", "High Volume + High Gain"], ["mk-hvloss", "High Volume + Top Losers"], ["mk-relative", "Relative Outperformance / Underperformance"], ["mk-fiidii", "FII / DII Activity"], ["mk-swing", "Swing Market Watch"]];
{ let at = -1;
  for (const [id, h] of SECTIONS) { const m = new RegExp('<section class="mk-sec" id="' + id + '"><h[12][^>]*>' + h.replace(/[/+']/g, "\\$&") + "</h[12]>").exec(main); ok(m, "section " + id + " with heading " + h); ok(m.index > at, id + " is in the specified order"); at = m.index; eq((main.match(new RegExp('id="' + id + '"', "g")) || []).length, 1, id + " exists once"); }
  eq((main.match(/<section class="mk-sec"/g) || []).length, 13, "exactly the thirteen dashboard sections");
  // responsive redesign: All-Time High / Low and Sector Leaders / Laggards are no longer on the home page (their renderers and data are kept and still tested below)
  for (const id of ["mk-ath", "mk-sectorlead"]) no(html.slice(html.indexOf("<body>"), html.indexOf("<script")), new RegExp('id="' + id + '"'), id + " is not on the home page");
  no(main, /All-Time High \/ Low|Sector Leaders \/ Laggards/, "neither heading is on the home page");
  re(main, /<h1 class="mk-h1">Market Today<\/h1>/, "the page's one h1 is Market Today"); eq((html.match(/<h1\b/g) || []).length, 1, "and there is only one h1 in the page");
  eq((main.match(/<div data-mk>/g) || []).length, 13, "each section has its own content box that the module fills"); }
// the Fundamental screen is kept, after the dashboard
ok(html.indexOf('id="screener-sec"') > html.indexOf("</main>"), "the existing Fundamental screen follows the dashboard"); re(html, /<h2 id="screener">Fundamental screen<\/h2>/, "with its heading and id (the Fundamentals nav target)"); re(html, /id="ftabs"/, "and its tabs (the Financials nav target)");

// ---------- 2. the old descriptive homepage is gone ----------
{ const body = html.slice(html.indexOf("<body>"), html.indexOf("<script"));
  for (const t of ["A simple end-of-day stock screener", "id=\"demo\"", "id=\"all\"", "id=\"more\"", "id=\"news\"", "id=\"tools\"", "id=\"road\"", "id=\"cost\"", "id=\"money\"", "id=\"law\"", "id=\"pc\"", "Roadmap", "SEBI rules", "How StockLens makes money"]) ok(!body.includes(t), "old homepage block removed: " + t);
  no(body, /Not investment advice[\s\S]{0,40}<\/p>/, "no marketing or disclaimer paragraphs in the dashboard body");
  eq((body.match(/<footer>/g) || []).length, 1, "one footer"); re(body, /<footer>End-of-day market data\. Not investment advice\.<\/footer>/, "the footer says end-of-day"); }
{ const leg = legacy(html); let base = ""; try { base = cp.execSync("git show aa4ace1:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) {}
  if (base) { // the search module was changed on purpose later (universe search; test_company_search.js pins it): it is compared on its own, and everything else is still compared byte for byte
    const SRCHMOD = /<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, cnt = (h) => (h.match(new RegExp(SRCHMOD.source, "g")) || []).length;
    eq([cnt(leg), cnt(base)], [1, 1], "one search module in the page minus the 5I and 5J layers, and one in the Phase 5H.6 page");
    eq(leg.replace(SRCHMOD, "") === base.replace(SRCHMOD, ""), true, "the page minus the 5I and 5J layers and minus the search module is byte-for-byte the Phase 5H.6 page minus its search module: nothing else changed (Stock Detail, charts, fundamentals, financials, shareholding, research, compare)");
    eq(leg.indexOf('<script type="module" id="stocklens-search">') === base.indexOf('<script type="module" id="stocklens-search">'), true, "and the search module still sits at exactly the same place"); } }
no(main, /Quarterly Results|Buy\b|Sell\b|Strong Buy|Bearish|Bullish|Target price|Rating/, "no recommendations, ratings or quarterly results in the dashboard markup");
no(code.replace(/\/\*[\s\S]*?\*\//g, ""), /Strong Buy|Bearish|Bullish|Target price|Quarterly/, "none in the module either (the FII/DII Buy and Sell columns are institutional flows, not advice)");

// ---------- 3. the module ----------
global.window = {}; global.document = { getElementById: () => null, addEventListener() {} }; global.fetch = () => { throw new Error("no fetch in this test"); };
(0, eval)(code);
const M = window.SLMarket;
eq(Object.keys(M).sort(), ["HV_GAIN", "IDS", "LARGE_MOVE", "VOL_MULT", "WINDOWS", "activityOf", "breadth", "cleanCandles", "events", "fiidii", "gainers", "highs", "hvGain", "hvLoss", "indices", "losers", "model", "num", "perfOf", "relative", "sectionHtml", "sectorsOf", "shockers", "sig3", "stocksOf", "tracked", "vol"], "the public surface is small and fixed");
{ const OFF_HOME = ["mk-ath", "mk-sectorlead"];   // responsive redesign: removed from the home page; the module keeps their renderers, and paint() skips a section that has no box
  eq(M.IDS.filter((id) => !OFF_HOME.includes(id)), SECTIONS.map((s) => s[0]), "the module draws exactly the sections in the page, in order");
  eq(M.IDS.filter((id) => OFF_HOME.includes(id)), OFF_HOME, "the two removed sections are the only ids the page has no box for");
  re(code, /function paint\(id\)\{var box=document\.querySelector&&document\.querySelector\('#'\+id\+' \[data-mk\]'\);if\(box\)box\.innerHTML=/, "paint() writes nothing when a section has no box on the page"); }
eq(M.WINDOWS.map((w) => w.label), ["1D", "1W", "30D", "90D"], "the windows are 1D, 1W, 30D, 90D"); eq([M.LARGE_MOVE, M.VOL_MULT, M.HV_GAIN], [5, 2, 3], "the thresholds");
no(code, /Upstox|api\.|access_token|Bearer|apikey|api_key|secret|localStorage|sessionStorage|XMLHttpRequest|WebSocket|eval\(|new Function/i, "no API, token, storage, socket or dynamic code in the module");
eq([...code.matchAll(/getJson\("([^"]*)"\)/g)].map((m) => m[1]), ["out/market_sector_stocks.json", "out/scans.json", "out/historical.json", "out/fiidii_history.json", "out/market_sectors.json", "out/market_activity.json"], "it reads the three files the site already publishes, plus the two sector files that exist on the site only when sector display is approved (otherwise the fetch fails and the blocks say Sector data unavailable)");
no(html, /Bearer\s|access_token|UPSTOX_[A-Z_]+\s*=|api\.upstox\.com|Authorization/i, "no token or Upstox call anywhere in the page");

// --- perfOf: windows, calendar days, gaps ---
const day = (n) => new Date(Date.UTC(2026, 0, 1) + n * 864e5).toISOString().slice(0, 10);
const series = (n, f) => Array.from({ length: n }, (_, i) => ({ date: day(i), close: f(i) }));
{ const c = series(100, (i) => 100 + i), p = M.perfOf(c), last = 199;
  near(p.d1, (199 / 198 - 1) * 100, "1D is the previous trading day"); near(p.w1, (199 / 192 - 1) * 100, "1W is 7 calendar days back"); near(p.d30, (199 / 169 - 1) * 100, "30D is 30 calendar days back"); near(p.d90, (199 / 109 - 1) * 100, "90D is 90 calendar days back");
  eq([p.last, p.date], [199, day(99)], "the last close and its date are reported");
  const short = M.perfOf(series(20, (i) => 100 + i)); eq([short.d1 !== null, short.w1 !== null, short.d30, short.d90], [true, true, null, null], "a short history gives only the windows it covers: never a guess");
  eq(M.perfOf(series(1, () => 5)), null, "one candle: no performance"); eq(M.perfOf(null), null, "no list: null"); eq(M.perfOf([]), null, "empty: null");
  const gap = series(100, (i) => 100 + i).filter((x, i) => i < 60 || i > 80); const g = M.perfOf(gap); eq([g.w1 !== null, g.d30, g.d90 !== null], [true, null, true], "a history gap: the window whose base falls in it (30D, base 9 days before its target) has no value; the others still do");
  const big = [{ date: day(0), close: 100 }, { date: day(40), close: 110 }]; eq(M.perfOf(big).d1, null, "a previous candle more than 7 days back is not a 1D move"); eq(M.perfOf(big).d30, null, "and a base more than 7 days before the target is no 30D base");
  eq(M.cleanCandles([{ date: "2026-01-02", close: 5 }, { date: "2026-01-01", close: 4 }, { date: "2026-01-01", close: 9 }, { date: "bad", close: 3 }, { date: "2026-01-03", close: 0 }, { date: "2026-01-04", close: -2 }, { date: "2026-01-05", close: "x" }, null]), [{ date: "2026-01-01", close: 4 }, { date: "2026-01-02", close: 5 }], "candles are validated, de-duplicated and sorted"); }

// --- indices, tracked, relative ---
const HIST = { benchmark: { symbol: "NIFTY 50", candles: series(100, (i) => 1000 + i) }, stocks: { AAA: { candles: series(100, (i) => 100 + i * 2) }, BBB: { candles: series(100, (i) => 300 - i) }, "bad sym!": { candles: series(100, (i) => 10 + i) }, EMPTY: { candles: [] } } };
{ const ix = M.indices(HIST); eq(ix.length, 1, "one index (the only one the data holds)"); eq(ix[0].name, "NIFTY 50", "named from the data"); eq(M.indices({}), [], "no benchmark: no index, nothing invented"); eq(M.indices(null), [], "no file: none");
  eq(M.tracked(HIST).map((t) => t.symbol), ["AAA", "BBB"], "tracked stocks: valid symbols with a usable history, sorted");
  const r = M.relative(HIST, "w1"); eq(r.rows.map((x) => x.symbol), ["AAA", "BBB"], "relative rows for the tracked stocks"); near(r.bench, M.perfOf(HIST.benchmark.candles).w1, "the NIFTY return over the same window");
  for (const x of r.rows) near(x.rel, x.stock - x.bench, "relative = stock minus NIFTY, in percentage points: " + x.symbol);
  ok(r.rows.find((x) => x.symbol === "AAA").rel > 0 && r.rows.find((x) => x.symbol === "BBB").rel < 0, "AAA outperforms, BBB underperforms");
  eq(M.relative({ stocks: HIST.stocks }, "w1"), null, "no NIFTY, no relative performance"); eq(M.relative({ benchmark: { candles: series(3, (i) => 5 + i) }, stocks: HIST.stocks }, "d90"), null, "a window the NIFTY history does not cover: null"); }

// --- scans ---
const SCANS = { as_of: "2026-10-05",
  gainers: [{ symbol: "G1", price: 100, change_pct: 6.5, volume_x_avg: 2.5 }, { symbol: "G2", price: 50, change_pct: 2.1, volume_x_avg: 0.8 }, { symbol: "G3", price: 70, change_pct: 9.2, volume_x_avg: 3.1 }, { symbol: "ZERO", price: 5, change_pct: 0, volume_x_avg: 1 }, { symbol: "bad sym", price: 1, change_pct: 50 }, { symbol: "NOCHG", price: 4 }],
  losers: [{ symbol: "L1", price: 20, change_pct: -7.2, volume_x_avg: 3.4 }, { symbol: "L2", price: 40, change_pct: -1.1, volume_x_avg: 2.4 }, { symbol: "L3", price: 90, change_pct: -3.3, volume_x_avg: 1.0 }, { symbol: "FLAT", price: 9, change_pct: 0, volume_x_avg: 5 }],
  volume_gainers: [{ symbol: "V1", price: 10, change_pct: 4.2, volume: 3000000, volume_x_avg: 4 }, { symbol: "V2", price: 11, change_pct: 1.0, volume: 900000, volume_x_avg: 6 }, { symbol: "V3", price: 12, change_pct: 3.5, volume: 500000, volume_x_avg: 1.5 }, { symbol: "V4", price: 13, change_pct: 5, volume: 0, volume_x_avg: 9 }],
  high_52w: [{ symbol: "H1", price: 200, old_52w_high: 190, change_pct: 1.5 }, { symbol: "H2", price: 300, old_52w_high: 280, change_pct: 4.5 }],
  dma: [{ symbol: "D1", price: 50, dma50: 49, dma200: 48, status: "Golden cross today" }, { symbol: "D2", price: 50, dma50: 47, dma200: 48, status: "Death cross today" }, { symbol: "D3", price: 50, status: "Above both" }],
  breadth: { advancing: 1200, declining: 800, unchanged: 100, new_52w_highs: 33, above_50dma_pct: 55.5, above_200dma_pct: 61.2 } };
{ eq(M.gainers(SCANS).map((r) => r.symbol), ["G3", "G1", "G2"], "gainers: positive only, sorted by change, invalid symbols and rows without a change dropped");
  eq(M.losers(SCANS).map((r) => r.symbol), ["L1", "L3", "L2"], "losers: negative only, biggest fall first");
  eq(M.highs(SCANS).map((r) => r.symbol), ["H2", "H1"], "52-week highs sorted by change");
  eq(M.shockers(SCANS).map((r) => r.symbol), ["V2", "V1"], "volume shockers: 2x or more, traded volume > 0, highest multiple first (V3 is below 2x, V4 has no volume)");
  eq(M.hvGain(SCANS).map((r) => r.symbol), ["V1"], "high volume + high gain: 2x volume AND a gain of 3% or more (V2 gained only 1%)");
  eq(M.hvLoss(SCANS).map((r) => r.symbol), ["L1", "L2"], "high volume + losers: a loser with 2x volume or more, by multiple (L3 has 1.0x)");
  const edge = { volume_gainers: [{ symbol: "E1", change_pct: 3, volume: 10, volume_x_avg: 2 }, { symbol: "E2", change_pct: 2.99, volume: 10, volume_x_avg: 2 }, { symbol: "E3", change_pct: 3, volume: 10, volume_x_avg: 1.99 }], losers: [{ symbol: "E4", change_pct: -1, volume_x_avg: 2 }, { symbol: "E5", change_pct: -1, volume_x_avg: 1.99 }] };
  eq(M.hvGain(edge).map((r) => r.symbol), ["E1"], "thresholds are inclusive at exactly 3% and 2x and exclusive just below"); eq(M.hvLoss(edge).map((r) => r.symbol), ["E4"], "and 2x for losers");
  eq([M.gainers({}), M.losers(null), M.highs(undefined), M.shockers({ volume_gainers: "x" })], [[], [], [], []], "missing or malformed lists are empty, never an error");
  eq(M.breadth(SCANS), { adv: 1200, dec: 800, unch: 100, highs: 33, a50: 55.5, a200: 61.2 }, "breadth is read as given"); eq(M.breadth({ breadth: { advancing: 1, declining: 2 } }), null, "incomplete breadth: null"); eq(M.breadth({}), null, "no breadth: null"); }

// --- What's Happening: facts with rules ---
{ const ev = M.events(SCANS), types = (t) => ev.filter((e) => e.type === t).map((e) => e.symbol);
  eq(types("New 52-week high"), ["H2", "H1"], "52-week highs are events"); eq(types("Large move").sort(), ["G1", "G3", "L1"], "moves of 5% or more, up or down (G2 +2.1% and L3 -3.3% are not)");
  eq(types("Unusual volume").sort(), ["G1", "G3", "L1", "L2", "V1", "V2"], "volume 2x the average or more (from gainers, losers and the volume list)");
  eq(types("50-day crossed above 200-day"), ["D1"], "a golden cross is an event"); eq(types("50-day crossed below 200-day"), ["D2"], "and a death cross");
  eq(new Set(ev.map((e) => e.type + "|" + e.symbol)).size, ev.length, "no event is listed twice");
  ok(ev.every((e) => /^[A-Za-z0-9&._-]+$/.test(e.symbol) && e.text.length > 5), "every event names a symbol and states a fact");
  no(JSON.stringify(ev), /Buy|Sell|Bullish|Bearish|Target|Rating|breakout|rally|surge|plunge/i, "no advice and no hype in the event text");
  ok(ev.find((e) => e.symbol === "H1" && e.type === "New 52-week high").text.includes("190.00"), "the text carries the real previous high");
  eq(M.events({}), [], "no scan, no events"); }

// --- FII / DII ---
{ const F = [{ category: "DII", date: "03-Oct-2026", buyValue: "100", sellValue: "50", netValue: "50" }, { category: "FII/FPI", date: "03-Oct-2026", buyValue: "10", sellValue: "30", netValue: "-20" }, { category: "FII/FPI", date: "02-Oct-2026", buyValue: "1", sellValue: "1", netValue: "0" }, { category: "DII", date: "05-Oct-2026", buyValue: "1,000.5", sellValue: "400", netValue: "600.5" }];
  const f = M.fiidii(F); eq(f.date, "05-Oct-2026", "the latest date wins, whatever the order of the file (5 Oct is newer than 3 Oct)"); eq(f.dii, { buy: 1000.5, sell: 400, net: 600.5 }, "numbers with commas parse"); eq(f.fii, null, "FII is not mixed in from an older day: only the latest date is used");
  const g = M.fiidii(F.filter((x) => x.date !== "05-Oct-2026")); eq([g.date, g.fii.net, g.dii.net], ["03-Oct-2026", -20, 50], "both sides on the same date when both exist");
  eq(M.fiidii([{ category: "DII", date: "2026-10-04", netValue: "7" }]).dii.net, 7, "an ISO date also parses"); eq(M.fiidii([]), null, "empty: null"); eq(M.fiidii(null), null, "no file: null"); eq(M.fiidii([{ category: "DII", date: "garbage", netValue: "1" }]), null, "an unreadable date: null"); eq(M.fiidii([{ category: "Other", date: "03-Oct-2026", netValue: "1" }]), null, "an unknown category: null"); }

// ---------- 4. the sections as drawn ----------
const FII = [{ category: "DII", date: "05-Oct-2026", buyValue: "1000", sellValue: "400", netValue: "600" }, { category: "FII/FPI", date: "05-Oct-2026", buyValue: "500", sellValue: "900", netValue: "-400" }];
const model = M.model(SCANS, HIST, FII), empty = M.model(null, null, null);
const draw = (id, m = model, st = {}) => M.sectionHtml(id, m, st);
const hrefs = (h) => [...h.matchAll(/<a class="mk-sym" href="([^"]*)">([^<]*)<\/a>/g)].map((m) => [m[1], m[2]]);
{ const t = draw("mk-today");
  re(t, /Latest available market data: <b>2026-10-05<\/b> · end of day, not live/, "the date of the data is shown and it is not called live"); no(t.replace("not live", ""), /\blive\b/i, "nothing says Live");
  for (const l of ["1D", "1W", "30D", "90D"]) re(t, new RegExp("<small>" + l + "</small>"), "index performance column " + l);
  re(t, /<b>NIFTY 50<\/b><span class="v">1,099\.00<\/span>/, "NIFTY 50 with its latest value from the data"); re(t, /As of 2026-04-10/, "and the date of that value");
  eq((t.match(/class="mk-idx"/g) || []).length, 1, "one index card"); re(draw("mk-today", empty), /Data unavailable/, "no data: an honest unavailable state"); re(draw("mk-today", empty), /Latest available market data: <b>unavailable<\/b>/, "with no invented date"); }
// ---------- 4a. the NIFTY 50 / SENSEX tabs in Market Today ----------
{ const t = draw("mk-today"), s = draw("mk-today", model, { index: "SENSEX" }), tabs = (h) => [...h.matchAll(/<button type="button" role="tab" data-tab="([^"]*)" aria-selected="(true|false)">/g)].map((m) => [m[1], m[2]]);
  re(t, /<div class="tabs mk-tabs" role="tablist" data-mk-tabs="index">/, "the tab bar uses the page's existing tab mechanism (data-mk-tabs + data-tab)");
  eq(tabs(t), [["NIFTY 50", "true"], ["SENSEX", "false"]], "two tabs, NIFTY 50 selected by default"); eq(tabs(s), [["NIFTY 50", "false"], ["SENSEX", "true"]], "selecting SENSEX moves the selection");
  eq(tabs(draw("mk-today", model, { index: "BOGUS" })), tabs(t), "an unknown selection falls back to NIFTY 50");
  re(t, /<b>NIFTY 50<\/b><span class="v">1,099\.00<\/span>/, "the NIFTY 50 tab keeps the verified end-of-day card"); re(t, /end of day, not live/, "and says end of day, not live");
  no(s, /class="mk-idx"/, "the SENSEX tab draws no index card"); no(s, /class="mk-perf"/, "and no performance figures"); no(s, /\d[\d,]*\.\d{2}/, "and no SENSEX number at all");
  re(s, /SENSEX is not available yet: its data source has not been verified, so no value is shown/, "it says plainly that SENSEX is unavailable and why"); re(s, /end of day, not live/, "the end-of-day status line stays on the SENSEX tab");
  re(draw("mk-today", empty, { index: "SENSEX" }), /SENSEX is not available yet/, "SENSEX is unavailable with no data loaded either"); re(draw("mk-today", empty), /NIFTY 50 history is not loaded/, "NIFTY 50 without data keeps its fallback text");
  { const bare = (h) => h.replace(/\/\*[\s\S]*?\*\//g, ""); /* the key is written once, as a strict comparison against the FILE's own key: never requested, subscribed to or looked up */
    eq((bare(code).match(/BSE_INDEX/g) || []).length, 1, "the BSE key text appears once in the module code"); re(bare(code), /ins\.instrument_key!=="BSE_INDEX\|SENSEX"/, "as a comparison only");
    eq((bare(html).match(/BSE_INDEX/g) || []).length, 1, "and nowhere else in the page"); }
  /* the click itself is handled by the module's existing generic handler */
  ok(/\[data-mk-tabs\] \[data-tab\]/.test(code) && /STATE\[grp\]=b\.getAttribute\("data-tab"\)/.test(code), "the generic handler stores the chosen tab in STATE and repaints the section (no page reload, no navigation)");
  no(code.replace(/\/\*[\s\S]*?\*\//g, ""), /location\.(reload|href|assign|replace)/, "the module never reloads or navigates"); }
// ---------- 4b. the SENSEX end-of-day card: two closed gates, strict validation, its own date, lazy loading ----------
const vm = require("vm");
const GATE_OFF = "var SENSEX_PAGE_APPROVED=false;";
eq(code.split(GATE_OFF).length, 2, "the page-level SENSEX gate is declared once and ships closed (false)"); no(code, /SENSEX_PAGE_APPROVED\s*=\s*true/, "and is never set to true in the page");
eq((code.match(/SENSEX-EOD:start/g) || []).length, 2, "two marked SENSEX blocks (the card, and the lazy request)"); eq((code.match(/SENSEX-EOD:end/g) || []).length, 2, "each closed");
eq((code.replace(/\/\*[\s\S]*?\*\//g, "").match(/getJson\(SENSEX_FILE\)/g) || []).length, 1, "exactly one request for the SENSEX file"); re(code, /var SENSEX_FILE="out\/sensex_history\.json";/, "to its own file, not out/historical.json");
re(code, /public redistribution rights for BSE SENSEX data/i, "the rights uncertainty is stated in the page code"); re(code, /unverified candidate/, "and so is the unverified BSE metadata URL");
const loadMod = (text) => { const ctx = { window: {}, document: { getElementById: () => null, addEventListener() {} }, fetch() { throw new Error("no fetch in this test"); } }; vm.createContext(ctx); vm.runInContext(text, ctx); return ctx.window.SLMarket; };
const MC = loadMod(code), MO = loadMod(code.replace(GATE_OFF, "var SENSEX_PAGE_APPROVED=true;"));     /* MO: a test-only copy with the page gate OPEN. The shipped page is MC. */
const ymd = (end, back) => new Date(Date.parse(end + "T00:00:00Z") - back * 864e5).toISOString().slice(0, 10);
const sxCandles = (end, n = 120, base = 80000) => Array.from({ length: n }, (_, i) => { const c = base + i * 10; return { date: ymd(end, n - 1 - i), open: c, high: c + 50, low: c - 50, close: c, volume: null }; });
const sxDoc = (end = "2026-10-05", over = {}) => Object.assign({ schema_version: 1, kind: "sensex_history", as_of: end, last_date: end, instrument: { label: "SENSEX", instrument_key: "BSE_INDEX|SENSEX", instrument_name: "SENSEX", exchange: "BSE", segment: "BSE_INDEX", instrument_type: "INDEX" },
  data_rights_note: "Public redistribution rights for BSE SENSEX data are unconfirmed.", public_display_approved: true, candles: sxCandles(end) }, over);
const HIST2 = Object.assign({}, HIST, { benchmark: { symbol: "NIFTY 50", candles: sxCandles("2026-10-05", 120, 22000) } });     /* NIFTY ends on the same day as the scans: 2026-10-05 */
const modelO = (extra = {}) => Object.assign({}, MO.model(SCANS, HIST2, FII), extra);
const todayO = (m, tab) => MO.sectionHtml("mk-today", m, tab ? { index: tab } : {});
const UNAVAILABLE = "SENSEX is not available yet: its data source has not been verified, so no value is shown";
const isNa = (h) => h.includes('<p class="mk-na">Data unavailable<small>' + UNAVAILABLE + "</small></p>") && !/class="mk-idx"/.test(h);
const LOADING = '<p class="mk-na" role="status">Loading SENSEX end-of-day data\u2026</p>';
const isLoading = (h) => h.includes(LOADING) && !/class="mk-idx"|Data unavailable|mk-perf/.test(h);
const NOTE = (t) => '<p class="mk-sub">' + t + "</p>";
const N_LOAD = "The SENSEX end-of-day file could not be loaded.", N_BAD = "The SENSEX end-of-day data was not accepted for display.";
const N_NOREF = "There is no market date on this page to check the SENSEX end-of-day data against.";
const N_STALE = (d) => "The latest SENSEX end-of-day date (" + d + ") is more than 7 days behind the market data on this page.";
const f2 = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
{ // approved and valid, same day as NIFTY
  const d = sxDoc(), h = todayO(modelO({ sensex: d }), "SENSEX"), last = d.candles[d.candles.length - 1].close, prev = d.candles[d.candles.length - 2].close;
  re(h, new RegExp('<b>SENSEX</b><span class="v">' + f2(last).replace(/[.,]/g, "\\$&") + "</span>"), "the card shows SENSEX with ITS OWN last close"); re(h, /As of 2026-10-05/, "and its own latest trading date");
  for (const l of ["1D", "1W", "30D", "90D"]) re(h, new RegExp("<small>" + l + "</small>"), "performance column " + l); re(h, new RegExp("\\+" + ((last / prev - 1) * 100).toFixed(2).replace(".", "\\.") + "%"), "1D change computed from SENSEX's own previous close");
  no(h, /not the same trading day/, "no date warning when the day matches NIFTY"); eq((h.match(/class="mk-idx"/g) || []).length, 1, "one card"); no(h, /<b>NIFTY 50<\/b>/, "no NIFTY card on the SENSEX tab when the days match"); no(h, /Data unavailable/, "and not the unavailable message");
  eq(MO.sectionHtml("mk-today", modelO({ sensex: d }), { index: "SENSEX" }).includes("end of day, not live"), true, "the status line still says end of day, not live"); }
{ // a date difference from NIFTY is flagged, in both directions, and NIFTY's own date is not borrowed
  for (const end of ["2026-10-02", "2026-10-06"]) { const h = todayO(modelO({ sensex: sxDoc(end) }), "SENSEX");
    re(h, new RegExp("As of " + end), "SENSEX keeps its own date " + end); re(h, new RegExp("SENSEX is as of <b>" + end + "</b>; NIFTY 50 is as of <b>2026-10-05</b>\\. These are not the same trading day\\."), "and the difference from NIFTY is stated: " + end); } }
{ // never NIFTY values: invalid SENSEX data shows the plain message, with the NIFTY model fully loaded
  const m = modelO(), nlast = f2(m.indices[0].perf.last);
  ok(isLoading(todayO(m, "SENSEX")), "no sensex field yet (gate open): the loading line, nothing else"); no(todayO(m, "SENSEX"), new RegExp(nlast.replace(/[.,]/g, "\\$&")), "and no NIFTY value on the SENSEX tab");
  const withCard = todayO(modelO({ sensex: sxDoc("2026-10-02") }), "SENSEX"); no(withCard, new RegExp(">" + nlast.replace(/[.,]/g, "\\$&") + "<"), "a valid SENSEX card never carries NIFTY's number"); no(withCard, /<b>NIFTY 50<\/b>/, "or NIFTY's card"); }
{ // unavailable for every kind of bad input
  const bad = { "string": "x", "number": 5, "array": [], "empty object": {},
    "flag false": sxDoc("2026-10-05", { public_display_approved: false }), "flag missing": (() => { const d = sxDoc(); delete d.public_display_approved; return d; })(), "flag the string true": sxDoc("2026-10-05", { public_display_approved: "true" }),
    "flag 1": sxDoc("2026-10-05", { public_display_approved: 1 }), "flag null": sxDoc("2026-10-05", { public_display_approved: null }),
    "wrong kind": sxDoc("2026-10-05", { kind: "index_history" }), "schema 2": sxDoc("2026-10-05", { schema_version: 2 }), "schema string": sxDoc("2026-10-05", { schema_version: "1" }),
    "other key": sxDoc("2026-10-05", { instrument: { label: "SENSEX", instrument_key: "BSE_INDEX|OTHER" } }), "nse key": sxDoc("2026-10-05", { instrument: { label: "SENSEX", instrument_key: "NSE_INDEX|Nifty 50" } }),
    "wrong label": sxDoc("2026-10-05", { instrument: { label: "NIFTY 50", instrument_key: "BSE_INDEX|SENSEX" } }), "no instrument": sxDoc("2026-10-05", { instrument: null }),
    "candles not a list": sxDoc("2026-10-05", { candles: "x" }), "one candle": sxDoc("2026-10-05", { candles: sxCandles("2026-10-05", 1) }), "no candles": sxDoc("2026-10-05", { candles: [] }),
    "last_date mismatch": sxDoc("2026-10-05", { last_date: "2026-10-04" }), "as_of mismatch": sxDoc("2026-10-05", { as_of: "2026-10-06" }), "no as_of": sxDoc("2026-10-05", { as_of: undefined }) };
  const mk = (f) => { const d = sxDoc(); f(d.candles); return d; };
  Object.assign(bad, { "duplicate date": mk((c) => { c[50].date = c[49].date; }), "descending": mk((c) => { c.reverse(); }), "string close": mk((c) => { c[10].close = "80000"; }), "NaN close": mk((c) => { c[10].close = NaN; }),
    "zero close": mk((c) => { c[10].close = 0; }), "negative close": mk((c) => { c[10].close = -5; }), "null close": mk((c) => { c[10].close = null; }), "bad date": mk((c) => { c[10].date = "2026-13-45"; }),
    "date not a string": mk((c) => { c[10].date = 20260101; }), "a null candle": mk((c) => { c[10] = null; }), "Infinity close": mk((c) => { c[10].close = Infinity; }) });
  for (const [name, d] of Object.entries(bad)) { const h = todayO(modelO({ sensex: d }), "SENSEX");
    ok(isNa(h), "unavailable message for: " + name); ok(h.endsWith(NOTE(N_BAD)), "followed by the one line saying it was not accepted: " + name); no(h, /8\d,\d{3}|class="mk-idx"/, "and no value for: " + name); }
  { const h = todayO(modelO({ sensex: false }), "SENSEX"); ok(isNa(h), "failed fetch (false): the unavailable message"); ok(h.endsWith(NOTE(N_LOAD)), "followed by the line saying the file could not be loaded"); }
  for (const loading of [null, undefined]) ok(isLoading(todayO(modelO({ sensex: loading }), "SENSEX")), "waiting for the file (" + loading + "): the loading line, never blank, never the card"); }
{ // stale: more than 7 days behind the newest date the page knows
  for (const [end, shown] of [["2026-09-28", true], ["2026-09-27", false], ["2026-09-20", false], ["2026-04-10", false]]) { const h = todayO(modelO({ sensex: sxDoc(end) }), "SENSEX");
    eq(/class="mk-idx"/.test(h), shown, "SENSEX ending " + end + (shown ? " (7 days behind) is shown, with its date and the difference" : " is too old: unavailable")); if (!shown) { ok(isNa(h), "the plain message for " + end); ok(h.endsWith(NOTE(N_STALE(end))), "and the line naming SENSEX's own date " + end); } else re(h, /not the same trading day/, "and flagged"); }
  for (const [what, doc] of [["old", sxDoc("2026-03-01")], ["recent", sxDoc("2026-10-05")]]) {
    const noRef = Object.assign({}, MO.model(null, null, null), { asOf: null, indices: [], sensex: doc }), h = todayO(noRef, "SENSEX");
    eq(noRef.asOf, null, "fixture: no page market date"); eq(noRef.indices.length, 0, "fixture: no NIFTY date");
    ok(isNa(h), "no reference date at all (" + what + " SENSEX file, gate open, approved, valid): the exact unavailable message"); ok(h.endsWith(NOTE(N_NOREF)), "followed by the line saying there is no date to check against"); no(h, /class="mk-idx"|mk-perf|81,|8\d,\d{3}/, "and no SENSEX card or price"); }
}
{ // every state is visible and distinct, none is empty, and the shipped page is exactly the old message
  const states = { loading: undefined, "loading (null)": null, failed: false, bad: { kind: "x" }, stale: sxDoc("2026-04-10"), good: sxDoc() }, seen = new Set();
  for (const [name, d] of Object.entries(states)) { const h = todayO(modelO({ sensex: d }), "SENSEX"); const body = h.slice(h.indexOf("</div>") + 6);
    ok(body.length > 40 && /class="mk-na"|class="mk-idx"/.test(h), "gate open, " + name + ": the panel is not empty"); seen.add(body); }
  eq(seen.size, 5, "five different panels for six states (undefined and null are the same loading line)");
  const EXACT = '<p class="mk-na">Data unavailable<small>' + UNAVAILABLE + "</small></p>";
  for (const [name, d] of Object.entries(states)) { const m = Object.assign({}, MC.model(SCANS, HIST2, FII), { sensex: d }), h = MC.sectionHtml("mk-today", m, { index: "SENSEX" });
    ok(h.endsWith(EXACT), "gate closed, " + name + ": the panel is exactly the original message"); no(h, /Loading|could not be loaded|not accepted|days behind|no market date|class="mk-idx"/, "gate closed, " + name + ": no state wording and no card at all"); } }
{ // the shipped page (gate closed) never shows a value, whatever the file says
  const m = Object.assign({}, MC.model(SCANS, HIST2, FII), { sensex: sxDoc() }); ok(isNa(MC.sectionHtml("mk-today", m, { index: "SENSEX" })), "page gate closed + perfect file + flag true: still the unavailable message");
  no(MC.sectionHtml("mk-today", m, { index: "SENSEX" }), /8\d,\d{3}/, "no SENSEX number"); }
{ // NIFTY non-regression: the NIFTY tab does not depend on SENSEX in any way
  const base = todayO(modelO(), undefined);
  for (const sensex of [undefined, null, false, sxDoc(), sxDoc("2026-10-02"), { garbage: 1 }]) eq(todayO(modelO({ sensex }), undefined), base, "NIFTY tab output is identical whatever SENSEX holds");
  eq(MC.sectionHtml("mk-today", Object.assign({}, MC.model(SCANS, HIST2, FII), { sensex: sxDoc() }), {}), MC.sectionHtml("mk-today", MC.model(SCANS, HIST2, FII), {}), "and on the shipped page"); re(base, /<b>NIFTY 50<\/b><span class="v">/, "with its card"); }
{ // the SENSEX card is built exactly like the NIFTY card
  const same = { benchmark: { symbol: "NIFTY 50", candles: sxCandles("2026-10-05") }, stocks: {} }, m = Object.assign({}, MO.model(SCANS, same, FII), { sensex: sxDoc("2026-10-05") });
  const grid = (h) => (/<div class="mk-idx-grid">[\s\S]*?<p class="mk-sub">As of [^<]*<\/p><\/div><\/div>/.exec(h) || [""])[0];
  eq(grid(todayO(m, "SENSEX")).replace("<b>SENSEX</b>", "<b>NIFTY 50</b>"), grid(todayO(m)), "same data: the two cards differ only by the name"); ok(grid(todayO(m)).length > 200, "and the card is really there"); }
{ // the generated page text: markers reverse cleanly (the older pins rely on this)
  const L = legacy(html); no(L, /SENSEX/, "legacy() removes every SENSEX trace: the older byte-identity pins still see the pre-edit page"); no(L, /SENSEX-EOD/, "including the markers"); }

// lazy loading and initial page-load behavior, driven through the module's real init() with a fake document and fake fetch
const settle = () => new Promise((r) => setImmediate(r));
function boot(text, files) {
  const listeners = [], boxes = {}, fetched = [], doc = { getElementById: (id) => (id === "market" ? {} : null), addEventListener: (t, f) => listeners.push([t, f]),
    querySelector: (sel) => { const m = /^#([\w-]+) \[data-mk\]$/.exec(sel); return m ? (boxes[m[1]] = boxes[m[1]] || { innerHTML: "" }) : null; } };
  const ctx = { window: {}, document: doc, fetch: (u) => { fetched.push(u); const f = files[u]; return Promise.resolve(f === undefined ? { ok: false } : { ok: true, json: () => Promise.resolve(f) }); } };
  vm.createContext(ctx); vm.runInContext(text, ctx);
  const click = (tab, group = "index") => { const btn = { closest: () => ({ id: "mk-today" }), parentNode: { getAttribute: () => group }, getAttribute: () => tab };
    for (const [t, f] of listeners) if (t === "click") f({ target: { closest: (sel) => (sel === "[data-mk-tabs] [data-tab]" ? btn : null) } }); };
  return { boxes, fetched, click };
}
const FILES = (extra) => Object.assign({ "out/scans.json": SCANS, "out/historical.json": HIST2, "out/fiidii_history.json": FII, "out/market_sectors.json": null }, extra);
const FOUR = ["out/scans.json", "out/historical.json", "out/fiidii_history.json", "out/market_sectors.json", "out/market_activity.json"];   // the four files of before, plus the validated market-activity file (Stage 8C)
const lazy = (async () => {
  const wait = async () => { for (let i = 0; i < 8; i++) await settle(); };
  { // shipped page: the file is never requested and the tab shows the plain message
    const b = boot(code, FILES({ "out/sensex_history.json": sxDoc() })); await wait();
    eq(b.fetched, FOUR, "initial page load requests exactly the four existing files plus out/market_activity.json, and no SENSEX file"); b.click("NIFTY 50"); b.click("SENSEX"); await wait();
    eq(b.fetched, FOUR, "gate closed: selecting SENSEX makes no request"); ok(isNa(b.boxes["mk-today"].innerHTML), "and the tab shows the plain unavailable message"); }
  { // gate open: one request, only on first selection of SENSEX
    const b = boot(code.replace(GATE_OFF, "var SENSEX_PAGE_APPROVED=true;"), FILES({ "out/sensex_history.json": sxDoc() })); await wait();
    eq(b.fetched, FOUR, "initial load: still those five files only"); const nifty = b.boxes["mk-today"].innerHTML; re(nifty, /<b>NIFTY 50<\/b>/, "NIFTY tab drawn"); b.click("NIFTY 50"); await wait(); eq(b.fetched, FOUR, "selecting NIFTY 50 requests nothing");
    b.click("SENSEX"); b.click("SENSEX"); ok(isLoading(b.boxes["mk-today"].innerHTML), "right after the click, before the file answers: the loading line (not blank, not the unavailable message)"); await wait(); eq(b.fetched, FOUR.concat(["out/sensex_history.json"]), "selecting SENSEX (twice, quickly) makes exactly one request, to the SENSEX file");
    re(b.boxes["mk-today"].innerHTML, /<b>SENSEX<\/b><span class="v">/, "and the SENSEX card is drawn from it"); no(b.boxes["mk-today"].innerHTML, /Data unavailable/, "without the unavailable message");
    b.click("NIFTY 50"); await wait(); eq(b.boxes["mk-today"].innerHTML, nifty, "back on NIFTY: byte-identical to before SENSEX was ever opened"); b.click("SENSEX"); await wait(); eq(b.fetched.length, FOUR.length + 1, "reselecting SENSEX never requests again"); }
  { // gate open but the file is missing, or says not approved: one request, the plain message, never retried
    for (const [name, files] of [["missing", {}], ["not approved", { "out/sensex_history.json": sxDoc("2026-10-05", { public_display_approved: false }) }], ["invalid", { "out/sensex_history.json": { kind: "x" } }]]) {
      const b = boot(code.replace(GATE_OFF, "var SENSEX_PAGE_APPROVED=true;"), FILES(files)); await wait(); b.click("SENSEX"); await wait();
      eq(b.fetched.filter((u) => u === "out/sensex_history.json").length, 1, name + ": one request"); ok(isNa(b.boxes["mk-today"].innerHTML), name + ": the plain unavailable message");
      ok(b.boxes["mk-today"].innerHTML.endsWith(NOTE(name === "missing" ? N_LOAD : N_BAD)), name + ": with its own reason line"); no(b.boxes["mk-today"].innerHTML, /Loading/, name + ": no longer loading"); b.click("SENSEX"); await wait(); eq(b.fetched.length, FOUR.length + 1, name + ": no retry"); } }
  { // another tab group never triggers the request
    const b = boot(code.replace(GATE_OFF, "var SENSEX_PAGE_APPROVED=true;"), FILES({ "out/sensex_history.json": sxDoc() })); await wait(); b.click("SENSEX", "trending"); await wait(); eq(b.fetched, FOUR, "a SENSEX-named tab in another group does nothing"); }
})();

{ const b = draw("mk-breadth");
  for (const l of ["Advances", "Declines", "Unchanged", "52-week highs", "52-week lows", "Upper circuits", "Lower circuits"]) re(b, new RegExp("<span>" + l + "</span>"), "breadth tile " + l);
  re(b, /1,200 advances/, "advances from the data"); re(b, /800 declines/, "declines"); re(b, /100 unchanged/, "unchanged"); re(b, /<b>33<\/b><span>52-week highs<\/span>/, "52-week highs from the data");
  eq((b.match(/Data unavailable/g) || []).length, 3, "52-week lows and both circuits are honestly unavailable (no data for them), not zero");
  re(b, /<i class="a" style="width:57\.14%"><\/i><i class="u" style="width:4\.76%"><\/i><i class="d" style="width:38\.10%"><\/i>/, "the bar widths are computed from the counts (1200, 100, 800 of 2100)"); re(b, /Above 50-day average<\/span>/, "50-day share"); }
{ const h = draw("mk-happening");
  ok(hrefs(h).length >= 8, "the feed has clickable stocks"); for (const [href, s] of hrefs(h)) eq(href, "#stock=" + encodeURIComponent(s), "feed link routes to the Stock Detail: " + s);
  re(h, /Rules: new 52-week high; move of 5% or more; volume 2x the 20-day average or more/, "the rules are written on the feed"); re(draw("mk-happening", empty), /Data unavailable/, "no scan: unavailable"); }
{ for (const id of ["mk-ath"]) { const h = draw(id); re(h, /Data unavailable/, id + " says Data unavailable"); re(h, /needs the complete listing history of every stock/, id + " says what is missing: the complete listing history"); no(h, /<table|class="up"|class="dn"|[+-]\d+\.\d\d%/, id + " shows no invented numbers"); no(h, /year/i, id + " never calls a one-year or five-year history all-time"); }
  for (const id of ["mk-sectors", "mk-sectorlead"]) { const h = draw(id); eq(h, '<p class="mk-na">Sector data unavailable</p>', id + " says exactly Sector data unavailable while no approved sector file is loaded"); } }
{ const h = draw("mk-movers"), a = h.split("Top Losers")[0], b = h.split("Top Losers")[1];
  eq(hrefs(a).map((x) => x[1]), ["G3", "G1", "G2"], "Top Gainers rows"); eq(hrefs(b).map((x) => x[1]), ["L1", "L3", "L2"], "Top Losers rows");
  for (const l of ["Stock", "Price", "Change", "Vol vs avg"]) re(h, new RegExp("<th[^>]*>" + l + "</th>"), "column " + l);
  re(a, /<td class="n"><span class="up">\+9\.20%<\/span>/, "G3 day change as given, green"); re(b, /<span class="dn">-7\.20%<\/span>/, "L1 as given, red"); re(a, /70\.00/, "LTP from the data");
  for (const [href, s] of hrefs(h)) eq(href, "#stock=" + s, "stock link routes: " + s); eq(hrefs(draw("mk-movers", M.model({ gainers: Array.from({ length: 30 }, (_, i) => ({ symbol: "S" + i, price: 1, change_pct: 30 - i })) }, null, null))).length, 10, "at most ten rows per list");
  eq((draw("mk-movers", empty).match(/Data unavailable/g) || []).length, 2, "no scan: both cards unavailable"); }
{ const h = draw("mk-trending");
  eq([...h.matchAll(/data-tab="([^"]*)"/g)].map((m) => m[1]), ["movers", "volume", "52w", "active"], "the four trending tabs (no All-Time High / Low tab on the home page)"); for (const l of ["Price Movers", "Volume Shockers", "52W High / Low", "Most Active"]) ok(h.includes(">" + l + "<"), "tab " + l); no(h, /All-Time High \/ Low/, "no All-Time tab button");
  re(h, /aria-selected="true">Price Movers/, "Price Movers is selected first");
  eq(hrefs(h).map((x) => x[1]).slice(0, 3), ["G3", "L1", "G1"], "movers are ordered by size of move, up or down");
  eq(hrefs(draw("mk-trending", model, { trend: "volume" })).map((x) => x[1]), ["V2", "V1"], "Volume Shockers tab"); const w = draw("mk-trending", model, { trend: "52w" }); eq(hrefs(w).map((x) => x[1]), ["H2", "H1"], "52W tab lists the highs"); re(w, /New 52-week low<\/h4><p class="mk-na">Data unavailable/, "and says the lows are unavailable");
  re(draw("mk-trending", model, { trend: "ath" }), /Data unavailable<small>needs the complete listing history of every stock/, "All-Time tab: unavailable, with the accurate reason"); re(draw("mk-trending", model, { trend: "active" }), /Data unavailable<small>the validated market-activity file is not available/, "Most Active tab: unavailable while the validated file is not loaded"); }
{ const h = draw("mk-52w"); eq(hrefs(h).map((x) => x[1]), ["H2", "H1"], "New 52-Week High table"); re(h, /Prev 52W high/, "with the previous high"); re(h, /New 52-Week Low[\s\S]*Data unavailable/, "52-week low is unavailable, not empty"); }
{ const v = draw("mk-volume"); eq(hrefs(v).map((x) => x[1]), ["V2", "V1"], "Volume Shockers rows"); re(v, /7\.50 L/, "V1: 3,000,000 / 4 = 7.50 L"); re(v, /1\.50 L/, "V2: 900,000 / 6 = 1.50 L"); re(v, /6\.0x/, "the multiple");
  eq(hrefs(draw("mk-hvgain")).map((x) => x[1]), ["V1"], "High Volume + High Gain"); eq(hrefs(draw("mk-hvloss")).map((x) => x[1]), ["L1", "L2"], "High Volume + Top Losers");
  for (const id of ["mk-volume", "mk-hvgain", "mk-hvloss"]) re(draw(id, empty), /Data unavailable/, id + " with no scan: unavailable");
  eq(M.sig3(150000), 150000, "sig3 keeps three significant digits"); eq(M.sig3(123456), 123000, "and rounds the rest"); eq(M.sig3(0), null, "zero: none"); eq(M.vol(12345678), "1.23 Cr", "crore format"); eq(M.vol(250000), "2.50 L", "lakh format"); eq(M.vol(null), "-", "no volume: a dash"); }
{ const r = draw("mk-relative"); eq([...r.matchAll(/data-tab="([^"]*)"/g)].map((m) => m[1]), ["d1", "w1", "d30", "d90"], "relative tabs 1D/1W/30D/90D"); re(r, /aria-selected="true">1W/, "1W is the default (prioritised)");
  const o = r.split("Relative Underperformance")[0], u = r.split("Relative Underperformance")[1]; eq(hrefs(o).map((x) => x[1]), ["AAA"], "outperformer"); eq(hrefs(u).map((x) => x[1]), ["BBB"], "underperformer");
  re(r, /pts<\/span>/, "relative shown in percentage points"); re(r, /Covers the 2 tracked stocks, not the whole market/, "the coverage is stated"); re(r, /as of 2026-04-10/, "with the date");
  re(draw("mk-relative", model, { rel: "d90" }), /aria-selected="true">90D/, "tab selection follows state"); re(draw("mk-relative", empty), /Data unavailable/, "no history: unavailable"); no(draw("mk-relative"), /Buy|Sell|Strong|Bullish|Bearish|Target/, "no advice words"); }
{ const f = draw("mk-fiidii"); re(f, /NSE figures for <b>05-Oct-2026<\/b> · end of day, ₹ crore/, "the date is labelled"); re(f, /\+600/, "DII net"); re(f, /-400|−400/, "FII net"); re(f, /<span class="up">\+600<\/span>/, "positive green"); re(f, /<span class="dn">-400<\/span>/, "negative red"); re(draw("mk-fiidii", empty), /Data unavailable/, "no file: unavailable"); }
{ const w = (k) => draw("mk-swing", model, { swing: k });
  eq([...w("d1").matchAll(/data-tab="([^"]*)"/g)].map((m) => m[1]), ["d1", "w1", "d30", "d90"], "swing tabs 1D/1W/30D/90D");
  re(w("d1"), /Strongest performers[\s\S]*Weakest performers[\s\S]*New 52-week high[\s\S]*Near 52-week low[\s\S]*High-volume positive performers/, "1D swing cards"); re(w("d1"), /Near 52-week low<\/h3><div class="mk-na|Near 52-week low[\s\S]{0,80}Data unavailable/, "near 52-week low is unavailable, not invented");
  const s30 = w("d30"); eq(hrefs(s30.split("Weakest performers")[0]).map((x) => x[1]), ["AAA", "BBB"], "30D strongest: tracked stocks, best first"); eq(hrefs(s30.split("Weakest performers")[1].split("Near 52-week high")[0]).map((x) => x[1]), ["BBB", "AAA"], "weakest: worst first"); re(s30, /Tracked stocks \(2\)/, "coverage stated");
  re(w("d90"), /Highest 3-month returns|Data unavailable/, "90D has its own card or an honest state"); re(draw("mk-swing", empty, { swing: "w1" }), /Data unavailable/, "no history: unavailable"); }
{ for (const id of M.IDS) { const h = draw(id, empty); ok(h.length > 10, id + " draws something with no data at all"); no(h, /undefined|NaN|\[object|null/, id + " leaks no undefined/NaN/null"); no(draw(id), /undefined|NaN|\[object|>null</, id + " with data: no undefined/NaN/null"); }
  eq(M.sectionHtml("mk-today", { indices: [{ name: "X", perf: null }] }, {}).includes("could not be drawn"), true, "a block that throws shows a plain unavailable note, not a crash"); eq(M.sectionHtml("nope", model, {}).includes("could not be drawn"), true, "an unknown block too");
  const xi = M.model(null, { benchmark: { symbol: "<img src=x>", candles: series(10, (i) => 5 + i) } }, null); no(draw("mk-today", xi), /<img/, "an index name from the data is escaped"); re(draw("mk-today", xi), /&lt;img src=x&gt;/, "(shown as text)");
  const evil = M.model({ gainers: [{ symbol: "A<img>", price: 1, change_pct: 1 }, { symbol: "OK", price: "<b>", change_pct: 1 }] }, null, null); const eh = draw("mk-movers", evil); no(eh, /<img|<b>[^<]*<\/b>>/, "symbols and values are escaped or rejected"); eq(hrefs(eh).map((x) => x[1]), ["OK"], "a symbol with markup is dropped, a valid one stays"); }

// ---------- 5. nothing hardcoded ----------
{ const body = main + css; no(body, /\d{1,3},\d{3}\.\d\d|\b\d{2,}\.\d{2}%/, "no market value or percentage is written in the page's markup or styles");
  const stat = code.replace(/\/\*[\s\S]*?\*\//g, ""); no(stat, /\b(RELIANCE|TCS|INFY|HDFCBANK|ICICIBANK|SBIN|NIFTY\s*50\s*[:=]|SENSEX|BANKNIFTY)\b\s*[:=,]\s*[\d"]/, "no stock or index with a value is written in the module");
  no(stat, /Math\.random|Date\.now|new Date\(\)/, "no random numbers and no clock: the date shown is the data's");
  no(stat, /"(Live|LIVE)"|>Live<|Live market|real-time/i, "nothing is labelled Live"); }

// ---------- 8C. the validated market-activity file: 52-week lows and Most Active (Stage 8C) ----------
const ACT_DATE = "2026-10-05";   // the date of SCANS, i.e. of the rest of the page
const actRow = (i, extra = {}) => Object.assign({ rank: i + 1, symbol: "A" + i, close: 100 + i, prev_close: 99 + i, change: 1, change_pct: 1.01, volume: 1000000 - i * 1000, turnover_lakhs: 50000 - i * 100, delivery_pct: 40 }, extra);
const lowRow = (i, extra = {}) => Object.assign({ rank: i + 1, symbol: "LW" + i, close: 90 - i, prior_low_52w: 100, prior_low_date: "2026-03-02", distance_pct: Math.round(((90 - i) / 100 - 1) * 10000) / 100, state: "new_low" }, extra);
const actDoc = (date = ACT_DATE) => ({ schema_version: 1, kind: "market_activity", as_of: date, source: "NSE EOD bhav data", freshness: "EOD",
  freshness_detail: { label: "EOD as of " + date, source_date: date, generated_at: date + "T21:00:00+05:30", source_is_latest_trading_day: true, market_session_open_at_generation: false, note: "End-of-day data: the last close, not real-time prices." },
  generated_at: date + "T21:00:00+05:30", universe: "NSE EQ series", universe_count: 2100, filters: {}, data_quality: { status: "pass", errors: [], warnings: [] },
  metric_status: { most_active: "safe", new_52w_high: "safe", new_52w_low: "safe", volume_vs_20d_average: "safe" },
  most_active: { by_value: { ranked_by: "turnover_lakhs (traded value, Rs lakhs)", rows: Array.from({ length: 12 }, (_, i) => actRow(i)) },
                 by_volume: { ranked_by: "volume (traded quantity, shares)", rows: Array.from({ length: 12 }, (_, i) => actRow(i, { symbol: "Q" + i })) } },
  high_low_52w: { new_high: { count: 0, rows: [] }, near_high: { count: 0, rows: [] }, new_low: { count: 3, rows: [lowRow(0), lowRow(1), lowRow(2)] }, near_low: { count: 0, rows: [] }, window: "previous 252 trading days; the latest day is not in its own window", low_column_available: true } });
const mut = (f, date) => { const d = actDoc(date); f(d); return d; };
const mA = (doc, scans = SCANS) => M.model(scans, HIST, FII, null, null, doc);
const lowPart = (h) => h.split("New 52-Week Low")[1] || "";
const actTab = (m) => draw("mk-trending", m, { trend: "active" });
{ // a good file draws the lows and Most Active, with the data date and clear ranking labels
  const m = mA(actDoc()); eq(m.act.state, "ok", "a good file is accepted"); eq(M.activityOf(actDoc(), ACT_DATE).state, "ok", "the public checker agrees");
  const w = draw("mk-52w", m); eq(hrefs(w).map((x) => x[1]), ["H2", "H1", "LW0", "LW1", "LW2"], "New 52-Week High (from the scan, unchanged) then the validated lows");
  re(w, /Prev 52W low/, "with the previous low"); re(lowPart(w), /<span class="dn">-10\.00%<\/span>/, "and the distance below it, as in the file"); re(lowPart(w), /as of <b>2026-10-05<\/b>, not live/, "the data date is shown and it is not called live");
  no(lowPart(w), /Data unavailable/, "no unavailable message once the data is accepted"); re(lowPart(w), /252 trading days/, "the rule is written on the card"); re(lowPart(w), /left out, not estimated/, "and the exclusions are stated, not hidden");
  eq(hrefs(draw("mk-trending", m, { trend: "52w" })).map((x) => x[1]), ["H2", "H1", "LW0", "LW1", "LW2"], "the 52W High / Low tab shows the same lows");
  const t = actTab(m); eq(hrefs(t).length, 20, "ten rows in each of the two rankings"); eq(hrefs(t).slice(0, 10).map((x) => x[1]), Array.from({ length: 10 }, (_, i) => "A" + i), "by traded value, in the file's order"); eq(hrefs(t).slice(10).map((x) => x[1]), Array.from({ length: 10 }, (_, i) => "Q" + i), "by traded quantity, in the file's order");
  re(t, /By traded value \(turnover, ₹ lakh\)[\s\S]*By traded quantity \(shares\)/, "two clearly labelled rankings"); re(t, /<th class="n">Turnover<\/th>/, "the value column is Turnover"); re(t, /<th class="n">Volume<\/th>/, "the quantity column is Volume"); re(t, /50,000\.00/, "the first turnover as in the file"); re(t, /10\.00 L/, "the first volume as in the file (1,000,000 shares)");
  re(t, /all 2,100 NSE EQ-series stocks[\s\S]*as of <b>2026-10-05<\/b>, not live/, "the universe size and the data date are on the card"); no(t, /Data unavailable/, "nothing unavailable"); no(t.replace(/not live/g, ""), /\blive\b/i, "never called live");
  { const low12 = mA(mut((d) => { d.high_low_52w.new_low = { count: 12, rows: Array.from({ length: 12 }, (_, i) => lowRow(i)) }; })); const h = lowPart(draw("mk-52w", low12)); eq(hrefs(h).length, 10, "at most ten lows are listed"); re(h, /Showing 10 of 12\./, "and the card says it is showing part of the list"); }
  { const none = mA(mut((d) => { d.high_low_52w.new_low = { count: 0, rows: [] }; })); const h = lowPart(draw("mk-52w", none)); re(h, /No new 52-week lows in the latest scan\./, "a good file with no lows says so"); no(h, /Data unavailable|<table/, "and is not shown as unavailable"); } }
{ // missing, stale, other-day or unusable files: the plain unavailable state with the reason, and no value of any kind
  const WHY_MISSING = /Data unavailable<small>the validated market-activity file is not available/;
  for (const [name, m] of [["not loaded", M.model(SCANS, HIST, FII)], ["null (404)", mA(null)], ["false", mA(false)], ["undefined", mA(undefined)]]) { re(lowPart(draw("mk-52w", m)), WHY_MISSING, name + ": lows unavailable"); re(actTab(m), WHY_MISSING, name + ": Most Active unavailable"); no(draw("mk-52w", m) + actTab(m), /LW\d|A0|Q0/, name + ": no value"); }
  { const m = mA(actDoc("2026-10-02")); eq(m.act.state, "date", "a file from another day is not used"); re(lowPart(draw("mk-52w", m)), /dated 2026-10-02, but the market data on this page is dated 2026-10-05/, "and the card says both dates"); re(actTab(m), /dated 2026-10-02, but the market data on this page is dated 2026-10-05/, "Most Active says the same"); no(draw("mk-52w", m) + actTab(m), /LW\d|A0|Q0/, "no value from the other day"); }
  { const m = M.model(null, HIST, FII, null, null, actDoc()); eq(m.act.state, "date", "no page date to check against: not used"); re(actTab(m), /no market date to check the validated file \(2026-10-05\) against/, "and it says why"); }
  { const m = mA(mut((d) => { d.freshness_detail.source_is_latest_trading_day = false; })); eq(m.act.state, "stale", "not from the latest trading day: not used"); re(actTab(m), /is not from the latest trading day/, "with the reason"); no(draw("mk-52w", m), /LW\d/, "and no lows"); }
  { const m = mA(mut((d) => { d.freshness_detail.market_session_open_at_generation = true; })); eq(m.act.state, "stale", "generated while the session was open: not used"); }
  { const sun = "2026-10-04", m = M.model(Object.assign({}, SCANS, { as_of: sun }), HIST, FII, null, null, actDoc(sun)); eq(m.act.state, "invalid", "a weekend date is refused even when it matches the page"); } }
{ // every top-level defect refuses the whole file
  const TOP = { "wrong kind": (d) => { d.kind = "market_breadth"; }, "schema 2": (d) => { d.schema_version = 2; }, "schema as text": (d) => { d.schema_version = "1"; }, "freshness LIVE": (d) => { d.freshness = "LIVE"; }, "as_of not a date": (d) => { d.as_of = "05-10-2026"; },
    "label differs": (d) => { d.freshness_detail.label = "EOD as of 2026-10-01"; }, "source_date differs": (d) => { d.freshness_detail.source_date = "2026-10-01"; }, "latest flag as text": (d) => { d.freshness_detail.source_is_latest_trading_day = "true"; }, "no freshness_detail": (d) => { delete d.freshness_detail; },
    "quality failed": (d) => { d.data_quality.status = "fail"; }, "quality has errors": (d) => { d.data_quality.errors = ["x"]; }, "no quality block": (d) => { delete d.data_quality; }, "universe 999": (d) => { d.universe_count = 999; }, "universe as text": (d) => { d.universe_count = "2100"; }, "universe fractional": (d) => { d.universe_count = 2100.5; },
    "no metric_status": (d) => { delete d.metric_status; } };
  for (const [name, f] of Object.entries(TOP)) { const m = mA(mut(f)); eq(m.act.state, "invalid", name + ": the file is refused"); re(lowPart(draw("mk-52w", m)), /Data unavailable<small>the validated market-activity file was not accepted/, name + ": lows say it was not accepted"); re(actTab(m), /Data unavailable<small>the validated market-activity file was not accepted/, name + ": Most Active says it too"); no(draw("mk-52w", m) + actTab(m), /LW\d|A0|Q0|<table[^>]*>(?:(?!H[12]).)*A0/, name + ": no value drawn"); }
  for (const bad of [[], "x", 7, true]) eq(mA(bad).act.state, "invalid", "a non-object document is refused: " + JSON.stringify(bad)); }
{ // each block is checked on its own: a bad Most Active list does not take the lows down, and the reverse
  const AC = { "rank gap": (d) => { d.most_active.by_value.rows[1].rank = 3; }, "not sorted": (d) => { d.most_active.by_volume.rows[1].volume = 5e9; }, "duplicate symbol": (d) => { d.most_active.by_value.rows[2].symbol = "A0"; }, "symbol with markup": (d) => { d.most_active.by_value.rows[0].symbol = "A<img src=x>"; },
    "zero close": (d) => { d.most_active.by_value.rows[0].close = 0; }, "close as text": (d) => { d.most_active.by_value.rows[0].close = "100"; }, "turnover as text": (d) => { d.most_active.by_value.rows[0].turnover_lakhs = "50000"; }, "volume negative": (d) => { d.most_active.by_volume.rows[0].volume = -1; },
    "change as text": (d) => { d.most_active.by_value.rows[0].change_pct = "1"; }, "empty list": (d) => { d.most_active.by_volume.rows = []; }, "rows not a list": (d) => { d.most_active.by_value.rows = {}; }, "wrong ranking label": (d) => { d.most_active.by_value.ranked_by = "name"; }, "no by_volume": (d) => { delete d.most_active.by_volume; },
    "no most_active": (d) => { delete d.most_active; }, "status unavailable": (d) => { d.metric_status.most_active = "unavailable"; }, "status missing": (d) => { delete d.metric_status.most_active; } };
  for (const [name, f] of Object.entries(AC)) { const m = mA(mut(f)); eq(m.act.state, "ok", name + ": the file itself is fine"); eq(m.act.active, null, name + ": Most Active is refused"); ok(m.act.low !== null, name + ": the lows are still accepted");
    const t = actTab(m); re(t, /Data unavailable<small>the (file does not mark the most-active lists as safe to use|most-active lists in the file were not accepted)/, name + ": the card says why"); no(t, /<table|A0|Q0/, name + ": and shows no partial list"); re(draw("mk-52w", m), /LW0/, name + ": the lows are still drawn"); }
  const LO = { "close not below prior": (d) => { d.high_low_52w.new_low.rows[0].close = 100; d.high_low_52w.new_low.rows[0].distance_pct = 0; }, "close above prior": (d) => { d.high_low_52w.new_low.rows[0].close = 101; }, "state near_low": (d) => { d.high_low_52w.new_low.rows[0].state = "near_low"; },
    "distance positive": (d) => { d.high_low_52w.new_low.rows[0].distance_pct = 5; }, "distance inconsistent": (d) => { d.high_low_52w.new_low.rows[0].distance_pct = -9; }, "count below rows": (d) => { d.high_low_52w.new_low.count = 2; }, "rows without count": (d) => { d.high_low_52w.new_low.rows = []; d.high_low_52w.new_low.count = 5; },
    "rank gap": (d) => { d.high_low_52w.new_low.rows[1].rank = 5; }, "duplicate symbol": (d) => { d.high_low_52w.new_low.rows[1].symbol = "LW0"; }, "symbol with markup": (d) => { d.high_low_52w.new_low.rows[0].symbol = "<img src=x>"; }, "prior date bad": (d) => { d.high_low_52w.new_low.rows[0].prior_low_date = "soon"; },
    "prior as text": (d) => { d.high_low_52w.new_low.rows[0].prior_low_52w = "100"; }, "no prior": (d) => { delete d.high_low_52w.new_low.rows[0].prior_low_52w; }, "count as text": (d) => { d.high_low_52w.new_low.count = "3"; }, "no new_low block": (d) => { delete d.high_low_52w.new_low; }, "no high_low block": (d) => { delete d.high_low_52w; },
    "low column missing": (d) => { d.high_low_52w.low_column_available = false; }, "status missing": (d) => { delete d.metric_status.new_52w_low; }, "status unknown": (d) => { d.metric_status.new_52w_low = "maybe"; } };
  for (const [name, f] of Object.entries(LO)) { const m = mA(mut(f)); eq(m.act.state, "ok", name + ": the file itself is fine"); eq(m.act.low, null, name + ": the lows are refused"); ok(m.act.active !== null, name + ": Most Active is still accepted");
    const w = draw("mk-52w", m); re(lowPart(w), /Data unavailable<small>/, name + ": the low card says unavailable"); no(lowPart(w), /LW\d|<table|-10\.00%/, name + ": and shows no partial list"); eq(hrefs(w).map((x) => x[1]), ["H2", "H1"], name + ": the high card (from the scan) is untouched"); re(actTab(m), /A0/, name + ": Most Active is still drawn"); }
  for (const [st, why] of [["insufficient_history", /not enough price history to find 52-week lows/], ["requires_adjustment", /suspected corporate action are left out/], ["unavailable", /do not carry the daily low prices/]]) { const m = mA(mut((d) => { d.metric_status.new_52w_low = st; })); re(lowPart(draw("mk-52w", m)), why, "status " + st + " keeps its own plain reason"); no(lowPart(draw("mk-52w", m)), /LW\d/, "and no low is drawn for " + st); } }
{ // nothing else on the page moves: every other block is byte-identical with or without the file, and the 52-week high card is the scan's
  const w0 = M.model(SCANS, HIST, FII), w1 = mA(actDoc());
  for (const id of M.IDS.filter((x) => x !== "mk-52w")) eq(draw(id, w1), draw(id, w0), id + ": unchanged by the validated file"); for (const tab of ["movers", "volume", "ath"]) eq(draw("mk-trending", w1, { trend: tab }), draw("mk-trending", w0, { trend: tab }), "trending tab " + tab + ": unchanged");
  eq(draw("mk-52w", w1).split("New 52-Week Low")[0], draw("mk-52w", w0).split("New 52-Week Low")[0], "the New 52-Week High card is the scan's, unchanged");
  eq(w1.highs, w0.highs, "highs come from scans.json only"); eq([w1.gainers, w1.losers, w1.shockers], [w0.gainers, w0.losers, w0.shockers], "movers and shockers come from scans.json only"); }
{ // All-Time High / Low: accurate wording, never unlocked by any file, never a calculation
  const ATH = /Data unavailable<small>needs the complete listing history of every stock; the saved price history is partial \(a limited period, for only some stocks\), so no all-time high or low is shown<\/small>/;
  for (const m of [model, empty, mA(actDoc()), mA(null)]) { const h = draw("mk-ath", m); eq((h.match(new RegExp(ATH.source, "g")) || []).length, 2, "both All-Time cards carry the accurate wording"); re(draw("mk-trending", m, { trend: "ath" }), ATH, "and so does the All-Time tab"); no(h, /<table|class="up"|class="dn"|\d{2,}\.\d\d/, "no all-time figure is invented"); }
  no(code, /current data holds about one year|needs the full price history of every stock/, "the misleading messages are gone from the module"); no(code.replace(/\/\*[\s\S]*?\*\//g, ""), /all-time[^"']{0,40}(five|5|one|1)[- ]year|(five|5|one|1)[- ]year[^"']{0,40}all-time/i, "no one-year or five-year history is called all-time"); }
{ // the module reads the new file only through getJson, and nothing else was added
  eq((code.match(/getJson\("out\/market_activity\.json"\)/g) || []).length, 1, "exactly one read of out/market_activity.json"); eq((code.match(/\bfetch\(/g) || []).length, 1, "still a single fetch call (inside getJson)");
  eq(code.replace(/\/\*[\s\S]*?\*\//g, "").match(/["']out\/[^"']*["']/g).filter((x, i, a) => a.indexOf(x) === i).sort(), ['"out/fiidii_history.json"', '"out/historical.json"', '"out/market_activity.json"', '"out/market_sector_stocks.json"', '"out/market_sectors.json"', '"out/scans.json"', '"out/sensex_history.json"'].sort(), "the module names exactly these files: the five it reads at load, the sector-stocks file it reads on a click, and the SENSEX file that only its own closed gate may request; no other");
  re(html, /var SENSEX_PAGE_APPROVED=false;/, "the SENSEX page gate is still closed"); }
const actLazy = (async () => {
  const wait = async () => { for (let i = 0; i < 8; i++) await settle(); };
  { const b = boot(code, FILES({ "out/market_activity.json": actDoc() })); await wait(); eq(b.fetched.filter((u) => u === "out/market_activity.json").length, 1, "the page requests the validated file once");
    re(b.boxes["mk-52w"].innerHTML, /LW0[\s\S]*LW1[\s\S]*LW2/, "and draws the lows from it"); const a = b.boxes["mk-trending"].innerHTML; no(a, /A0/, "the trending box still opens on Price Movers"); re(b.boxes["mk-movers"].innerHTML, /G3/, "other blocks are drawn as before"); }
  { const b = boot(code, FILES({})); await wait(); eq(b.fetched.filter((u) => u === "out/market_activity.json").length, 1, "a missing file is requested once");
    re(lowPart(b.boxes["mk-52w"].innerHTML), /the validated market-activity file is not available/, "and the lows say it is not available"); re(b.boxes["mk-movers"].innerHTML, /G3/, "while every other block still draws"); re(b.boxes["mk-52w"].innerHTML, /H2[\s\S]*H1/, "including the New 52-Week High card"); }
  { const b = boot(code, FILES({ "out/market_activity.json": { kind: "market_activity", bogus: true } })); await wait(); re(lowPart(b.boxes["mk-52w"].innerHTML), /was not accepted/, "an unusable file is reported as not accepted"); no(b.boxes["mk-52w"].innerHTML, /LW\d/, "and nothing from it is drawn"); }
  { const b = boot(code, FILES({ "out/market_activity.json": actDoc("2026-10-02") })); await wait(); re(lowPart(b.boxes["mk-52w"].innerHTML), /dated 2026-10-02, but the market data on this page is dated 2026-10-05/, "a file from another day is reported, not drawn"); no(b.boxes["mk-52w"].innerHTML, /LW\d/, "with no values"); } })();

// ---------- 6. responsive: the dashboard adapts to its own width (no media rules; the older tests pin those) ----------
{ re(css, /\.mk-sec\{/, "section style"); no(css, /@media/, "no new media rules"); re(css, /@container \(max-width:330px\)\{\.mk-t \.c3\{display:none\}\}/, "narrow cards drop their optional column"); re(css, /@container \(max-width:300px\)\{\.mk-perf\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)\}\}/, "narrow index card (under 300px, e.g. a 320-360px phone): the performance grid goes to two columns so a value like -0.71% never breaks before its %");
  re(css, /\.mk-g\{[^}]*repeat\(auto-fit,minmax\(min\(/, "grids use auto-fit with a min() so a column is never wider than the screen"); re(css, /\.mk-2,\.mk-g\{[^}]*repeat\(auto-fit,minmax\(min\(/, "the two-card rows wrap");
  no(css, /(?<![-\w])(min-)?width\s*:\s*\d{3,}px/, "no fixed wide widths"); eq(css.match(/white-space\s*:\s*nowrap/g).length, 1, "nowrap is used once only, for short numeric table cells"); re(css, /\.mk-t th\.n,\.mk-t td\.n\{[^}]*white-space:nowrap/, "(the right-aligned number columns)");
  re(css, /\.mk-t\{[^}]*width:100%/, "tables fill their card"); re(css, /overflow-wrap:anywhere|word-break/, "long text wraps"); re(css, /\.mk-sec\{[^}]*container-type:inline-size|container-type:inline-size/, "sections are containers");
  eq(css.match(/\.mk-[a-z0-9-]+/g).filter((c, i, a) => a.indexOf(c) === i).length > 20, true, "the dashboard has its own namespaced classes (mk-*)"); }

// ---------- 7. one global navigation, stock detail routing intact ----------
{ eq((html.match(/<nav\b/g) || []).length, 1, "exactly one <nav> in the page (the global bar)"); eq((html.match(/id="globalBar"/g) || []).length, 1, "one global bar"); eq((html.match(/id="companySearch"/g) || []).length, 1, "one search input"); eq((html.match(/class="gb-brand"/g) || []).length, 1, "one brand");
  ok(html.indexOf('<div id="globalBar">') < html.indexOf('<main id="market">'), "the global bar comes before the dashboard"); no(main, /<nav\b|<header\b|id="globalBar"/, "the dashboard carries no navigation or header of its own");
  const nav = modOf(html, '<script type="module" id="stocklens-nav">'); re(nav, /\$\("mk-swing"\)/, "Technical goes to Swing Market Watch"); re(nav, /\$\("screener"\)/, "Fundamentals to the Fundamental screen");
  for (const pin of ['id="detail"', "Stock Detail view", 'id="stocklens-search"', 'id="stocklens-compare"', 'id="stocklens-shareholding"', 'id="stocklens-snapshot"', 'id="stocklens-growth"', "Financial Health"]) ok(html.includes(pin), "still present: " + pin);
  re(html, /\.detail-mode \.w>\*:not\(#detail\)\{display:none!important\}/, "an open stock hides the dashboard (it is inside .w)"); ok(html.indexOf('<main id="market">') > html.indexOf('<div class="w">'), "the dashboard is inside .w, so Stock Detail and Compare modes hide it as they hid the old homepage");
  const w = html.slice(html.indexOf('<div class="w">')); ok(!/<main id="market">[\s\S]*<\/main>[\s\S]*<main id="market">/.test(w), "one dashboard"); }
// ---------- 9. a NIFTY 50 value older than the market data is labelled stale, never shown as current ----------
{ const HN = (end) => Object.assign({}, HIST, { benchmark: { symbol: "NIFTY 50", candles: sxCandles(end, 120, 22000) } });
  const view = (end, scans = SCANS, tab) => MC.sectionHtml("mk-today", MC.model(scans, HN(end), FII), tab ? { index: tab } : {});
  const money = (v) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fresh = view("2026-10-05");
  no(fresh, /mk-stale|Stale:/, "NIFTY on the same day as the scans: no stale notice"); re(fresh, /<p class="mk-sub">As of 2026-10-05<\/p>/, "and its own date is shown");
  no(view("2026-10-06"), /mk-stale|Stale:/, "NIFTY newer than the scans: no stale notice");
  const old = view("2026-10-02");
  re(old, /<p class="mk-sub">As of 2026-10-02<\/p>/, "an older NIFTY keeps showing its own real date");
  re(old, /<p class="mk-sub mk-stale">Stale: this NIFTY 50 value is from <b>2026-10-02<\/b>, older than the market data dated <b>2026-10-05<\/b>\. It is the last value available and is not current\.<\/p>/, "and a stale notice naming both dates");
  const lastClose = sxCandles("2026-10-02", 120, 22000).slice(-1)[0].close;
  ok(old.includes(money(lastClose)), "the value shown is still that day's close (nothing is replaced, estimated or relabelled)"); eq((old.match(/Stale:/g) || []).length, 1, "one notice only");
  no(old, /\bLIVE\b|Live\b/, "nothing is called live"); re(old, /end of day, not live/, "the status line still says end of day, not live");
  no(view("2026-10-02", Object.assign({}, SCANS, { as_of: null })), /mk-stale|Stale:/, "no market date to compare against: no notice (and no claim)");
  no(view("2026-10-02", Object.assign({}, SCANS, { as_of: "not a date" })), /mk-stale|Stale:/, "an unusable market date: no notice");
  const missing = MC.sectionHtml("mk-today", MC.model(SCANS, Object.assign({}, HIST, { benchmark: null }), FII), {});
  re(missing, /NIFTY 50 history is not loaded/, "no benchmark at all: the plain unavailable message"); no(missing, /Stale:|class="mk-idx"/, "with no invented value");
  no(MC.sectionHtml("mk-today", MC.model(SCANS, HN("2026-10-02"), FII), { index: "SENSEX" }), /Stale: this NIFTY 50/, "the SENSEX tab never carries the NIFTY notice");
  // the 9 -> 12 October incident: history stuck on Friday while the scans are Monday's; once the history advances the notice goes and the shown date is the last candle's
  { const sc = Object.assign({}, SCANS, { as_of: "2026-10-12" }), stuck = view("2026-10-09", sc), moved = view("2026-10-12", sc);
    re(stuck, /Stale: this NIFTY 50 value is from <b>2026-10-09<\/b>, older than the market data dated <b>2026-10-12<\/b>/, "stuck on 9 Oct against 12 Oct scans: labelled stale with both dates");
    no(moved, /mk-stale|Stale:/, "advanced to 12 Oct: no stale notice"); re(moved, /<p class="mk-sub">As of 2026-10-12<\/p>/, "and 12 Oct, the last candle, is the date shown");
    eq(MC.indices(HN("2026-10-12"))[0].perf.date, "2026-10-12", "the benchmark series ends on the displayed date"); }
  // the shown value and date come from the same candle as the benchmark's last entry
  const ix = MC.indices(HN("2026-10-02")); eq([ix.length, ix[0].perf.date, ix[0].perf.last], [1, "2026-10-02", lastClose], "indices(): last candle date and close agree with what is displayed"); }

Promise.all([lazy, actLazy]).then(() => console.log("Market dashboard tests passed (" + checks + " checks)"));
