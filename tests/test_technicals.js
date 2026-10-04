// Run: node tests/test_technicals.js   (no network, no browser; stubs the DOM and fetch)
// Tests the Phase 3 Step 3 Technical Snapshot. Every expected value is hand-calculated from the synthetic data described next to it.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot"));
const detailCode = blocks.find((b) => b.includes("Stock Detail view"));
const chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart"));
assert.ok(techCode && detailCode && chartCode, "snapshot, detail and chart script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      near = (a, b, tol, m) => { checks++; assert.ok(typeof a === "number" && Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };

// ---- load the pure API
global.window = {}; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
const T = new Function(techCode.replace('"use strict";', "").replace("(function(){", "var window={};(function(){").replace(/\}\)\(\);\s*$/, "})();return window.SLTech;"))();
ok(T && typeof T.compute === "function", "snapshot API exposed");

// ---- dataset A: one candle per CALENDAR day, i = 0..399 from 2025-01-01 (last = 2026-02-04, i = 399)
//      close = 100 + i, open = close, high = close + 1, low = close - 1, volume = 1000 + i
const day = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
const mkA = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const docOf = (candles, sym = "TCS") => ({ updated: "2026-10-01", source: "Upstox", stocks: { [sym]: { symbol: sym, candles } } });
const A = T.compute(docOf(mkA()), "TCS");
eq(A.asOf, "2026-02-04", "as-of date is the last candle date"); eq(day("2025-01-01", 399), "2026-02-04", "fixture check: i=399 is 2026-02-04");
// 1. last close
eq(A.lastClose, 499, "last close = close of the latest candle");
// 2. 1-day change = 499/498 - 1
near(A.change1d, (499 / 498 - 1) * 100, 1e-9, "1-day change %"); near(A.change1d, 0.200803, 1e-5, "1-day change % (value)");
// 3. returns - cutoffs: 1M 2026-01-04 (i=368, close 468), 3M 2025-11-04 (i=307, 407), 6M 2025-08-04 (i=215, 315), 1Y 2025-02-04 (i=34, 134)
eq([T.minusMonths("2026-02-04", 1), T.minusMonths("2026-02-04", 3), T.minusMonths("2026-02-04", 6), T.minusMonths("2026-02-04", 12)], ["2026-01-04", "2025-11-04", "2025-08-04", "2025-02-04"], "calendar cutoffs");
near(A.returns["1M"], (499 / 468 - 1) * 100, 1e-9, "1M return"); near(A.returns["3M"], (499 / 407 - 1) * 100, 1e-9, "3M return");
near(A.returns["6M"], (499 / 315 - 1) * 100, 1e-9, "6M return"); near(A.returns["1Y"], (499 / 134 - 1) * 100, 1e-9, "1Y return");
// 4. 52-week range: dates 2025-02-04..2026-02-04 (i=34..399): high = 500 (i=399), low = 134 - 1 = 133 (i=34)
eq(A.range.high, 500, "52-week high uses candle highs"); eq(A.range.low, 133, "52-week low uses candle lows");
near(A.range.fromHigh, (499 / 500 - 1) * 100, 1e-9, "distance from 52-week high (%)"); near(A.range.fromLow, (499 / 133 - 1) * 100, 1e-9, "distance from 52-week low (%)");
// 5. DMA comparison: SMA20 = mean(480..499) = 489.5, SMA50 = mean(450..499) = 474.5, SMA200 = mean(300..499) = 399.5
near(A.dma[20], (499 / 489.5 - 1) * 100, 1e-9, "close vs 20 DMA"); near(A.dma[50], (499 / 474.5 - 1) * 100, 1e-9, "close vs 50 DMA"); near(A.dma[200], (499 / 399.5 - 1) * 100, 1e-9, "close vs 200 DMA");
// 6. volume: latest 1399 / mean(volumes of i=379..398 = 1379..1398 = 1388.5)
near(A.volRatio, 1399 / 1388.5, 1e-12, "volume ratio uses the previous 20 candles, not including the latest");
// 7. ATR(14): every TR = 2 (H-L = 2, |H-prevC| = 2, |L-prevC| = 0) -> ATR = 2 -> 2 / 499
near(A.atrPct, 2 / 499 * 100, 1e-9, "ATR(14) % of last close");

// ---- ATR hand calculations (14 flat bars H11 L9 C10 -> ATR 2)
const flat14 = () => Array.from({ length: 14 }, (_, i) => ({ date: day("2026-01-01", i), open: 10, high: 11, low: 9, close: 10, volume: 1 }));
const bar = (i, h, l, c) => ({ date: day("2026-01-01", i), open: c, high: h, low: l, close: c, volume: 1 });
const atrPct = (extra) => T.compute(docOf(flat14().concat(extra)), "TCS").atrPct;
eq(T.atr(flat14().slice(0, 13), 14), null, "ATR needs 14 candles"); eq(T.compute(docOf(flat14().slice(0, 13)), "TCS").atrPct, null, "ATR(14) % shows - with 13 candles");
near(atrPct([bar(14, 26, 10, 20)]), 15, 1e-9, "TR 16 -> ATR (2*13+16)/14 = 3 -> 3/20 = 15%");
near(atrPct([bar(14, 26, 10, 20), bar(15, 22, 18, 20)]), 43 / 14 / 20 * 100, 1e-9, "Wilder smoothing: ATR (3*13+4)/14 = 43/14");
near(atrPct([bar(14, 8, 6, 7)]), 30 / 14 / 7 * 100, 1e-9, "gap down: TR = |L - prevC| = 4 -> ATR 30/14");
near(atrPct([bar(14, 18, 16, 17)]), 34 / 14 / 17 * 100, 1e-9, "gap up: TR = |H - prevC| = 8 -> ATR 34/14");

// ---- insufficient history: 30 calendar days from 2026-01-01
const short = T.compute(docOf(Array.from({ length: 30 }, (_, i) => ({ date: day("2026-01-01", i), open: 50 + i, high: 51 + i, low: 49 + i, close: 50 + i, volume: 100 + i }))), "TCS");
eq(short.returns["1M"], null, "1M return - when the history starts after the cutoff"); eq(short.returns["1Y"], null, "1Y return -"); eq(short.range, { high: null, low: null, fromHigh: null, fromLow: null }, "52-week range - when history is shorter than a year (not a partial range)");
eq(short.dma[50], null, "50 DMA - with 30 candles"); eq(short.dma[200], null, "200 DMA - with 30 candles"); near(short.dma[20], (79 / 69.5 - 1) * 100, 1e-9, "20 DMA still shown (mean of 60..79... = 69.5)");
near(short.volRatio, 129 / 118.5, 1e-9, "volume ratio with 30 candles (mean of 109..128 = 118.5)"); near(short.change1d, (79 / 78 - 1) * 100, 1e-9, "1-day change still shown");
// 52-week coverage is strict: the first candle must be dated on or before (last date - 365 days). Last date 2026-02-04 -> window start 2025-02-04.
const fromDay = (first) => Array.from({ length: 400 }, (_, i) => ({ date: day(first, i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const cover = (first, lastIdx) => T.compute(docOf(fromDay(first).slice(0, lastIdx)), "TCS");
{ const exact = T.compute(docOf(Array.from({ length: 366 }, (_, i) => ({ date: day("2025-02-04", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1 }))), "TCS");
  eq(exact.asOf, "2026-02-04", "fixture: last date 2026-02-04"); eq([exact.range.high, exact.range.low], [466, 99], "first candle exactly on the window start -> 52-week range shown");
  const late = (n) => T.compute(docOf(Array.from({ length: 366 - n }, (_, i) => ({ date: day("2025-02-04", i + n), open: 100 + i + n, high: 101 + i + n, low: 99 + i + n, close: 100 + i + n, volume: 1 }))), "TCS");
  eq(late(1).range, { high: null, low: null, fromHigh: null, fromLow: null }, "first candle ONE day after the window start -> - (no tolerance)");
  eq(late(5).range.fromHigh, null, "first candle 5 days after the window start -> -"); eq(late(10).range.high, null, "first candle 10 days after the window start -> - (old tolerance removed)"); }
// one candle only
const one = T.compute(docOf([{ date: "2026-01-02", open: 5, high: 6, low: 4, close: 5, volume: 10 }]), "TCS");
ok(one.change1d === null && one.volRatio === null && one.atrPct === null && one.dma[20] === null, "single candle: only last close is shown"); eq(one.lastClose, 5, "single candle last close");

// ---- no interpolation: candles on Jan 1 (100), Jan 2 (110), then Feb 10 (121). 1M cutoff = Jan 10 -> latest on/before = Jan 2 (110)
const gap = T.compute(docOf([{ date: "2026-01-01", open: 100, high: 100, low: 100, close: 100 }, { date: "2026-01-02", open: 110, high: 110, low: 110, close: 110 }, { date: "2026-02-10", open: 121, high: 121, low: 121, close: 121 }]), "TCS");
near(gap.returns["1M"], 10, 1e-9, "return uses the real close on or before the cutoff (no interpolation, no averaging)"); eq(gap.returns["3M"], null, "no candle before the 3M cutoff -> -");
// month-end clamp: 31 Mar minus 1 month is 28 Feb (not 3 Mar)
eq(T.minusMonths("2026-03-31", 1), "2026-02-28", "month-end cutoff is clamped"); eq(T.minusMonths("2024-03-31", 1), "2024-02-29", "leap-year clamp"); eq(T.minusMonths("2026-01-15", 2), "2025-11-15", "cutoff across a year boundary");
const mk = (d, c) => ({ date: d, open: c, high: c, low: c, close: c });
near(T.compute(docOf([mk("2026-02-27", 50), mk("2026-02-28", 60), mk("2026-03-01", 63), mk("2026-03-31", 66)]), "TCS").returns["1M"], (66 / 60 - 1) * 100, 1e-9, "1M cutoff for 31 Mar is 28 Feb -> reference close 60");

// ---- missing volume
const volA = (f) => { const c = mkA(); f(c); return T.compute(docOf(c), "TCS"); };
eq(volA((c) => { c[399].volume = null; }).volRatio, null, "latest volume missing -> -"); eq(volA((c) => { c[390].volume = null; }).volRatio, null, "a missing volume inside the 20-day window -> -");
near(volA((c) => { c[300].volume = null; }).volRatio, 1399 / 1388.5, 1e-12, "missing volume outside the window does not matter"); eq(volA((c) => c.forEach((x) => { x.volume = 0; })).volRatio, null, "all-zero volume -> - (no divide by zero)");
eq(volA((c) => c.forEach((x) => { delete x.volume; })).volRatio, null, "no volume field at all -> -"); near(volA((c) => { c[399].volume = 0; }).volRatio, 0, 1e-12, "a real zero latest volume is 0.00x, not missing");

// ---- invalid candles are ignored; an invalid LAST row never becomes the last close
const dirty = mkA().concat([{ date: "2026-02-05", open: 0, high: 5, low: 0, close: 0, volume: 1 }, { date: "bad", open: 1, high: 1, low: 1, close: 1 }, { date: "2026-02-06", open: 1, high: 0.5, low: 2, close: 1 },
  { date: "2026-02-07", open: NaN, high: 1, low: 1, close: 1 }, null, { date: "2026-02-04", open: 1, high: 2, low: 0.5, close: 1.5, volume: 5 }]);
const D = T.compute(docOf(dirty), "TCS"); ok(D.lastClose === 1.5 && D.asOf === "2026-02-04", "a duplicate date keeps the later row; other bad rows are dropped");
const dirty2 = mkA().concat([{ date: "2026-02-05", open: 0, high: 5, low: 0, close: 0, volume: 1 }, { date: "bad", open: 1, high: 1, low: 1, close: 1 }, { date: "2026-02-06", open: 1, high: 0.5, low: 2, close: 1 }, null]);
eq(T.compute(docOf(dirty2), "TCS"), A, "invalid rows change nothing: identical snapshot to the clean data");
const shuffled = mkA().reverse(); eq(T.compute(docOf(shuffled), "TCS"), A, "candles in any order give the same snapshot");

// ---- missing stock / data
eq(T.compute(docOf(mkA(), "TCS"), "INFY"), null, "stock not in the file -> null"); eq(T.compute(null, "TCS"), null, "null document -> null"); eq(T.compute({}, "TCS"), null, "empty document -> null");
eq(T.compute(docOf([]), "TCS"), null, "no candles -> null"); eq(T.compute(docOf([{ date: "x" }]), "TCS"), null, "only invalid candles -> null");

// ---- through the Stock Detail page
function stubs(hist, hash) {
  const els = {}, win = {}, fetched = [];
  const mkEl = (id) => ({ id, innerHTML: "", textContent: "", setAttribute() {}, addEventListener() {}, querySelector: () => null });
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => els[i] || (els[i] = mkEl(i)), addEventListener() {} };
  global.fetch = async (u) => { fetched.push(u); const f = u.endsWith("historical.json") ? hist : { as_of: "2026-10-01", source: "Upstox", stocks: [] }; return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
  return { els, win, fetched };
}
async function page(hist, hash = "#stock=TCS", withChart = false) {
  const s = stubs(hist, hash); eval(detailCode); eval(techCode); if (withChart) eval(chartCode);
  await new Promise((r) => setTimeout(r, 40)); return { ...s, detail: () => s.els.detail.innerHTML, tech: () => s.els.detailTech.innerHTML };
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tile = (h, label) => text((h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + label.replace(/[()%÷]/g, "\\$&") + "</span>")) || [])[1] || "NOT FOUND");
(async () => {
  let t = await page(docOf(mkA()));
  re(t.tech(), /<h3[^>]*>Technical Snapshot<\/h3>/, "section is labelled Technical Snapshot");
  eq(tile(t.tech(), "Last close (daily data)"), "₹499.00", "last close tile, labelled as daily data"); eq(tile(t.tech(), "1-day change"), "+0.20%", "1-day change tile");
  eq(tile(t.tech(), "Return 1M"), "+6.62%", "1M tile (499/468-1)"); eq(tile(t.tech(), "Return 3M"), "+22.60%", "3M tile (499/407-1)"); eq(tile(t.tech(), "Return 6M"), "+58.41%", "6M tile (499/315-1)"); eq(tile(t.tech(), "Return 1Y"), "+272.39%", "1Y tile (499/134-1)");
  eq(tile(t.tech(), "52-week high"), "₹500.00", "52-week high tile"); eq(tile(t.tech(), "52-week low"), "₹133.00", "52-week low tile"); eq(tile(t.tech(), "From 52-week high"), "-0.20%", "from-high tile"); eq(tile(t.tech(), "From 52-week low"), "+275.19%", "from-low tile");
  eq(tile(t.tech(), "Close vs 20 DMA"), "+1.94%", "vs 20 DMA tile"); eq(tile(t.tech(), "Close vs 50 DMA"), "+5.16%", "vs 50 DMA tile"); eq(tile(t.tech(), "Close vs 200 DMA"), "+24.91%", "vs 200 DMA tile");
  eq(tile(t.tech(), "Volume ÷ 20-day average"), "1.01×", "volume tile"); eq(tile(t.tech(), "ATR(14) as % of close"), "0.40%", "ATR tile");
  re(t.tech(), /Daily data as of 2026-02-04\. Historical data is not live\./, "as-of note with the latest candle date");
  // placement: Cash Flow -> Technical Snapshot container -> Price chart; other sections remain
  const d = t.detail(), pos = (s) => d.indexOf(s);
  ok(pos("Financial Health") > 0 && pos("Overview") > 0 && pos("Cash Flow") > pos("Financial Health"), "Overview, Financial Health and Cash Flow sections remain");
  ok(pos('id="detailTech"') > pos("Cash Flow") && pos('id="detailTech"') < pos("Price chart") && pos("Price chart") < pos('id="detailChart"'), "Technical Snapshot sits between Cash Flow and the Price chart");
  re(d, /Historical Price Chart — Coming in Phase 3 Step 2/, "chart container (Step 1 placeholder) still rendered for the chart module"); re(d, /LTP \(last close, ₹\)/, "existing LTP tile untouched"); re(d, /Today's change/, "existing Today's change tile untouched");
  ok(t.fetched.includes("out/historical.json") && !t.fetched.some((u) => /upstox/i.test(u)), "only out/historical.json is fetched for history");
  // no signal wording anywhere in the snapshot output or code
  const visible = text(t.tech()) + " " + techCode.replace(/\/\*[\s\S]*?\*\//g, "");
  no(visible, /\b(bullish|bearish|buy|sell|strong|weak|signal|breakout|oversold|overbought|recommend)/i, "no buy/sell/signal wording in the snapshot");
  // errors
  t = await page(docOf(mkA(), "INFY")); eq(text(t.tech()), "Technical Snapshot Historical data unavailable.", "no history for the stock -> Historical data unavailable.");
  t = await page(null); eq(text(t.tech()), "Technical Snapshot Historical data unavailable.", "historical.json missing -> Historical data unavailable."); re(t.detail(), /Stock Detail: TCS/, "rest of the page still renders");
  t = await page({ updated: "x" }); eq(text(t.tech()), "Technical Snapshot Historical data unavailable.", "malformed historical.json -> Historical data unavailable.");
  t = await page(docOf(Array.from({ length: 30 }, (_, i) => ({ date: day("2026-01-01", i), open: 50 + i, high: 51 + i, low: 49 + i, close: 50 + i, volume: 100 + i })))); eq(tile(t.tech(), "Return 1Y"), "-", "insufficient history shows - on the page"); eq(tile(t.tech(), "52-week high"), "-", "52-week high - on the page"); eq(tile(t.tech(), "Close vs 200 DMA"), "-", "200 DMA - on the page");
  // routing: the snapshot follows the stock in the hash
  const two = docOf(mkA(), "TCS"); two.stocks.INFY = { symbol: "INFY", candles: mkA().map((c) => ({ ...c, close: c.close * 2, open: c.open * 2, high: c.high * 2, low: c.low * 2 })) };
  t = await page(two, "#stock=INFY"); eq(tile(t.tech(), "Last close (daily data)"), "₹998.00", "INFY snapshot uses INFY candles");
  t = await page(two, "#stock=TCS"); eq(tile(t.tech(), "Last close (daily data)"), "₹499.00", "TCS snapshot uses TCS candles");
  // no stale paint: leaving the stock before the file arrives
  t = await page(two, "");  ok(!t.fetched.some((u) => u.endsWith("historical.json")), "dashboard (no #stock) does not fetch history");

  // ---- the existing chart and the other blocks are untouched
  const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
  eq(sha(chartCode), "a78129ddaf5bc8776090737e26b5fdff29a182a89d4ec8bd23d9c9fb5024a5d4", "Phase 3 Step 2 chart script block is byte-identical to the verified version");
  eq(sha(blocks[blocks.length - 1]), "35de0d215ec3947da870f95e636f41bf4b130d6d929be4d91d3deb2661344d2f", "final Phase 2B script block is byte-identical");
  eq(blocks.indexOf(techCode) > blocks.indexOf(chartCode) && blocks.indexOf(techCode) < blocks.length - 1, true, "snapshot block sits after the chart and before the final Phase 2B block");
  ok(blocks.find((b) => b === chartCode).includes("window.SLChart="), "chart module still exposes its API");
  const t2 = await page(docOf(mkA()), "#stock=TCS", true); ok(!!t2.els.detailChart, "chart container still exists with the chart module loaded");

  // ---- security / scope
  no(html, /UPSTOX_ANALYTICS_TOKEN|Bearer\s|Authorization/i, "no token or auth header in the page"); no(html, /api\.upstox\.com/, "no direct Upstox call in the page");
  no(techCode, /WebSocket|EventSource|setInterval|XMLHttpRequest/, "no live data or polling in the snapshot");
  eq((techCode.match(/fetch\(/g) || []).length, 1, "the snapshot makes exactly one fetch"); re(techCode, /fetch\("out\/historical\.json"\)/, "…and it is out/historical.json");

  console.log("Technicals tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
