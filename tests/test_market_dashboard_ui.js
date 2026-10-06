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
const SECTIONS = [["mk-today", "Market Today"], ["mk-breadth", "Market Breadth"], ["mk-happening", "What's Happening"], ["mk-sectors", "Sector Dashboard"], ["mk-movers", "Top Gainers / Top Losers"], ["mk-trending", "Trending Stocks"], ["mk-52w", "New 52-Week High / Low"], ["mk-ath", "All-Time High / Low"], ["mk-volume", "Volume Shockers"], ["mk-hvgain", "High Volume + High Gain"], ["mk-hvloss", "High Volume + Top Losers"], ["mk-relative", "Relative Outperformance / Underperformance"], ["mk-fiidii", "FII / DII Activity"], ["mk-sectorlead", "Sector Leaders / Laggards"], ["mk-swing", "Swing Market Watch"]];
{ let at = -1;
  for (const [id, h] of SECTIONS) { const m = new RegExp('<section class="mk-sec" id="' + id + '"><h[12][^>]*>' + h.replace(/[/+']/g, "\\$&") + "</h[12]>").exec(main); ok(m, "section " + id + " with heading " + h); ok(m.index > at, id + " is in the specified order"); at = m.index; eq((main.match(new RegExp('id="' + id + '"', "g")) || []).length, 1, id + " exists once"); }
  eq((main.match(/<section class="mk-sec"/g) || []).length, 15, "exactly the fifteen dashboard sections");
  re(main, /<h1 class="mk-h1">Market Today<\/h1>/, "the page's one h1 is Market Today"); eq((html.match(/<h1\b/g) || []).length, 1, "and there is only one h1 in the page");
  eq((main.match(/<div data-mk>/g) || []).length, 15, "each section has its own content box that the module fills"); }
// the Fundamental screen is kept, after the dashboard
ok(html.indexOf('id="screener-sec"') > html.indexOf("</main>"), "the existing Fundamental screen follows the dashboard"); re(html, /<h2 id="screener">Fundamental screen<\/h2>/, "with its heading and id (the Fundamentals nav target)"); re(html, /id="ftabs"/, "and its tabs (the Financials nav target)");

// ---------- 2. the old descriptive homepage is gone ----------
{ const body = html.slice(html.indexOf("<body>"), html.indexOf("<script"));
  for (const t of ["A simple end-of-day stock screener", "id=\"demo\"", "id=\"all\"", "id=\"more\"", "id=\"news\"", "id=\"tools\"", "id=\"road\"", "id=\"cost\"", "id=\"money\"", "id=\"law\"", "id=\"pc\"", "Roadmap", "SEBI rules", "How StockLens makes money"]) ok(!body.includes(t), "old homepage block removed: " + t);
  no(body, /Not investment advice[\s\S]{0,40}<\/p>/, "no marketing or disclaimer paragraphs in the dashboard body");
  eq((body.match(/<footer>/g) || []).length, 1, "one footer"); re(body, /<footer>End-of-day market data\. Not investment advice\.<\/footer>/, "the footer says end-of-day"); }
{ const leg = legacy(html); let base = ""; try { base = cp.execSync("git show aa4ace1:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) {}
  if (base) eq(leg === base, true, "the page minus the 5I and 5J layers is byte-for-byte the Phase 5H.6 page: nothing else changed (Stock Detail, charts, fundamentals, financials, shareholding, research, compare)"); }
no(main, /Quarterly Results|Buy\b|Sell\b|Strong Buy|Bearish|Bullish|Target price|Rating/, "no recommendations, ratings or quarterly results in the dashboard markup");
no(code.replace(/\/\*[\s\S]*?\*\//g, ""), /Strong Buy|Bearish|Bullish|Target price|Quarterly/, "none in the module either (the FII/DII Buy and Sell columns are institutional flows, not advice)");

// ---------- 3. the module ----------
global.window = {}; global.document = { getElementById: () => null, addEventListener() {} }; global.fetch = () => { throw new Error("no fetch in this test"); };
(0, eval)(code);
const M = window.SLMarket;
eq(Object.keys(M).sort(), ["HV_GAIN", "IDS", "LARGE_MOVE", "VOL_MULT", "WINDOWS", "breadth", "cleanCandles", "events", "fiidii", "gainers", "highs", "hvGain", "hvLoss", "indices", "losers", "model", "num", "perfOf", "relative", "sectionHtml", "shockers", "sig3", "tracked", "vol"], "the public surface is small and fixed");
eq(M.IDS, SECTIONS.map((s) => s[0]), "the module draws exactly the sections in the page");
eq(M.WINDOWS.map((w) => w.label), ["1D", "1W", "30D", "90D"], "the windows are 1D, 1W, 30D, 90D"); eq([M.LARGE_MOVE, M.VOL_MULT, M.HV_GAIN], [5, 2, 3], "the thresholds");
no(code, /Upstox|api\.|access_token|Bearer|apikey|api_key|secret|localStorage|sessionStorage|XMLHttpRequest|WebSocket|eval\(|new Function/i, "no API, token, storage, socket or dynamic code in the module");
eq([...code.matchAll(/getJson\("([^"]*)"\)/g)].map((m) => m[1]), ["out/scans.json", "out/historical.json", "out/fiidii_history.json"], "it reads only the three files the site already publishes");
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
{ const b = draw("mk-breadth");
  for (const l of ["Advances", "Declines", "Unchanged", "52-week highs", "52-week lows", "Upper circuits", "Lower circuits"]) re(b, new RegExp("<span>" + l + "</span>"), "breadth tile " + l);
  re(b, /1,200 advances/, "advances from the data"); re(b, /800 declines/, "declines"); re(b, /100 unchanged/, "unchanged"); re(b, /<b>33<\/b><span>52-week highs<\/span>/, "52-week highs from the data");
  eq((b.match(/Data unavailable/g) || []).length, 3, "52-week lows and both circuits are honestly unavailable (no data for them), not zero");
  re(b, /<i class="a" style="width:57\.14%"><\/i><i class="u" style="width:4\.76%"><\/i><i class="d" style="width:38\.10%"><\/i>/, "the bar widths are computed from the counts (1200, 100, 800 of 2100)"); re(b, /Above 50-day average<\/span>/, "50-day share"); }
{ const h = draw("mk-happening");
  ok(hrefs(h).length >= 8, "the feed has clickable stocks"); for (const [href, s] of hrefs(h)) eq(href, "#stock=" + encodeURIComponent(s), "feed link routes to the Stock Detail: " + s);
  re(h, /Rules: new 52-week high; move of 5% or more; volume 2x the 20-day average or more/, "the rules are written on the feed"); re(draw("mk-happening", empty), /Data unavailable/, "no scan: unavailable"); }
{ for (const id of ["mk-sectors", "mk-sectorlead", "mk-ath"]) { const h = draw(id); re(h, /Data unavailable/, id + " says Data unavailable"); re(h, /needs /, id + " says what is missing"); no(h, /<table|class="up"|class="dn"|[+-]\d+\.\d\d%/, id + " shows no invented numbers"); }
  re(draw("mk-sectors"), /needs daily prices for all NSE stocks/, "sectors: what a future data phase needs"); }
{ const h = draw("mk-movers"), a = h.split("Top Losers")[0], b = h.split("Top Losers")[1];
  eq(hrefs(a).map((x) => x[1]), ["G3", "G1", "G2"], "Top Gainers rows"); eq(hrefs(b).map((x) => x[1]), ["L1", "L3", "L2"], "Top Losers rows");
  for (const l of ["Stock", "Price", "Change", "Vol vs avg"]) re(h, new RegExp("<th[^>]*>" + l + "</th>"), "column " + l);
  re(a, /<td class="n"><span class="up">\+9\.20%<\/span>/, "G3 day change as given, green"); re(b, /<span class="dn">-7\.20%<\/span>/, "L1 as given, red"); re(a, /70\.00/, "LTP from the data");
  for (const [href, s] of hrefs(h)) eq(href, "#stock=" + s, "stock link routes: " + s); eq(hrefs(draw("mk-movers", M.model({ gainers: Array.from({ length: 30 }, (_, i) => ({ symbol: "S" + i, price: 1, change_pct: 30 - i })) }, null, null))).length, 10, "at most ten rows per list");
  eq((draw("mk-movers", empty).match(/Data unavailable/g) || []).length, 2, "no scan: both cards unavailable"); }
{ const h = draw("mk-trending");
  eq([...h.matchAll(/data-tab="([^"]*)"/g)].map((m) => m[1]), ["movers", "volume", "52w", "ath", "active"], "the five trending tabs"); for (const l of ["Price Movers", "Volume Shockers", "52W High / Low", "All-Time High / Low", "Most Active"]) ok(h.includes(">" + l + "<"), "tab " + l);
  re(h, /aria-selected="true">Price Movers/, "Price Movers is selected first");
  eq(hrefs(h).map((x) => x[1]).slice(0, 3), ["G3", "L1", "G1"], "movers are ordered by size of move, up or down");
  eq(hrefs(draw("mk-trending", model, { trend: "volume" })).map((x) => x[1]), ["V2", "V1"], "Volume Shockers tab"); const w = draw("mk-trending", model, { trend: "52w" }); eq(hrefs(w).map((x) => x[1]), ["H2", "H1"], "52W tab lists the highs"); re(w, /New 52-week low<\/h4><p class="mk-na">Data unavailable/, "and says the lows are unavailable");
  re(draw("mk-trending", model, { trend: "ath" }), /Data unavailable/, "All-Time tab: unavailable"); re(draw("mk-trending", model, { trend: "active" }), /Data unavailable/, "Most Active tab: unavailable"); }
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

// ---------- 6. responsive: the dashboard adapts to its own width (no media rules; the older tests pin those) ----------
{ re(css, /\.mk-sec\{/, "section style"); no(css, /@media/, "no new media rules"); re(css, /@container \(max-width:330px\)\{\.mk-t \.c3\{display:none\}\}/, "narrow cards drop their optional column"); re(css, /@container \(max-width:240px\)/, "very narrow: the performance grid goes to two columns");
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
console.log("Market dashboard tests passed (" + checks + " checks)");
