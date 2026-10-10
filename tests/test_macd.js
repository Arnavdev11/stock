// Run: node tests/test_macd.js   (no network, no browser; stubs the DOM, fetch and the TradingView library)
// Tests the Phase 3 Step 4 MACD (12, 26, 9) panel inside the chart. Expected values are hand-calculated (closed forms below).
const fs = require("fs"), assert = require("assert"), crypto = require("crypto");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
// Phase 5I: behaviour is tested on the page as shipped (html). The byte-identity pins below are tested on PINHTML = the page minus the Phase 5I layer (legacy_5i.js, proven exact against aa4ace1 by test_global_navigation.js), because Phase 5I deliberately changes the route readers and the search mount.
const PINHTML = require("./legacy_5i.js").legacy(html), PINBLOCKS = PINHTML.split("<script>").slice(1).map((b) => b.split("</script>")[0]), PINDETAIL = PINBLOCKS.find((b) => b.includes("Stock Detail view"));
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart"));
const detailCode = blocks.find((b) => b.includes("Stock Detail view"));
const techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot"));
assert.ok(chartCode && detailCode && techCode, "script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      near = (a, b, tol, m) => { checks++; assert.ok(typeof a === "number" && Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");

global.window = {}; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
const S = new Function(chartCode.replace('"use strict";', "").replace("(function(){", "var window={};(function(){").replace(/\}\)\(\);\s*$/, "})();return window.SLChart;"))();
ok(S && typeof S.macd === "function" && typeof S.prepare === "function", "MACD API exposed by the chart module");
eq([S.PARAMS.macdFast, S.PARAMS.macdSlow, S.PARAMS.macdSignal], [12, 26, 9], "parameters are 12 / 26 / 9");

// ---------- synthetic data ----------
const weekdays = (n, start) => { const out = [], d = new Date(start + "T00:00:00Z"); while (out.length < n) { if (d.getUTCDay() % 6) out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 1); } return out; };
const candlesFor = (closes, start = "2023-01-02") => weekdays(closes.length, start).map((date, i) => ({ date, open: closes[i], high: closes[i] + 0.5, low: closes[i] - 0.5, close: closes[i], volume: 1000 + i }));
const walkCloses = (n, p0, seed) => { let s = seed, p = p0; const out = []; for (let i = 0; i < n; i++) { s = (s * 1664525 + 1013904223) % 4294967296; p = p * (1 + (s / 4294967296 - 0.48) * 0.03); out.push(+p.toFixed(2)); } return out; };
const docOf = (candles, bench, sym = "TCS") => ({ updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: bench || candlesFor(Array.from({ length: candles.length }, (_, i) => 20000 + i), candles[0].date) }, stocks: { [sym]: { symbol: sym, candles } } });
const nn = (a) => a.filter((v) => v !== null).length, firstIdx = (a) => a.findIndex((v) => v !== null);

// ---------- 1. EMA12 / EMA26 (the existing EMA, SMA seed) ----------
const ramp = Array.from({ length: 60 }, (_, i) => i + 1);
const e12 = S.ema(ramp, 12), e26 = S.ema(ramp, 26);
near(e12[11], 6.5, 1e-9, "EMA12 seed = mean(1..12) = 6.5"); near(e12[12], 7.5, 1e-9, "EMA12 next value (k = 2/13) on a ramp"); eq(e12[10], null, "EMA12 undefined before index 11");
near(e26[25], 13.5, 1e-9, "EMA26 seed = mean(1..26) = 13.5"); eq(e26[24], null, "EMA26 undefined before index 25"); near(e26[59], 60 - 12.5, 1e-9, "EMA26 of a ramp = close - 12.5");

// ---------- 2. MACD / signal / histogram on the ramp: MACD = (c-5.5)-(c-12.5) = 7 exactly ----------
const M = S.macd(ramp);
eq([M.macd.length, M.signal.length, M.hist.length], [60, 60, 60], "all arrays have the input length");
eq(firstIdx(M.macd), 25, "MACD first defined at index 25"); eq(firstIdx(M.signal), 33, "Signal first defined at index 33"); eq(firstIdx(M.hist), 33, "Histogram first defined at index 33");
eq([M.macd[24], M.signal[32], M.hist[32]], [null, null, null], "nulls (not zeros) before the first defined index");
for (const i of [25, 26, 40, 59]) near(M.macd[i], 7, 1e-9, "MACD on a ramp is 7 at index " + i);
near(M.signal[33], 7, 1e-9, "Signal seed = mean of the first nine MACD values = 7"); near(M.signal[59], 7, 1e-9, "Signal stays 7 on a ramp");
near(M.hist[33], 0, 1e-9, "Histogram = MACD - Signal = 0 on a ramp"); near(M.hist[59], 0, 1e-9, "Histogram stays 0");

// ---------- 3. step series: 100 x40, then 110. e12(t)=110-10(11/13)^(t-39), e26(t)=110-10(25/27)^(t-39) ----------
const step = Array(40).fill(100).concat(Array(20).fill(110)), St = S.macd(step);
for (const i of [25, 33, 39]) eq(St.macd[i], 0, "MACD is exactly 0 while the price is flat (index " + i + ")");
for (const i of [33, 39]) eq([St.signal[i], St.hist[i]], [0, 0], "Signal and histogram 0 while flat (index " + i + ")");
const mt = (t) => 10 * (Math.pow(25 / 27, t - 39) - Math.pow(11 / 13, t - 39));
near(St.macd[40], 10 * (2 / 13 - 2 / 27), 1e-12, "MACD at the step bar = 10*(2/13 - 2/27)"); near(St.macd[40], 0.797720797, 1e-8, "MACD at the step bar (value)");
near(St.signal[40], 0.2 * mt(40), 1e-12, "Signal at the step bar = 0.2 * MACD (k = 0.2, previous 0)"); near(St.signal[40], 0.159544159, 1e-8, "Signal at the step bar (value)");
near(St.hist[40], 0.8 * mt(40), 1e-12, "Histogram at the step bar = 0.8 * MACD"); near(St.hist[40], 0.638176638, 1e-8, "Histogram at the step bar (value)");
near(St.macd[41], mt(41), 1e-12, "MACD one bar later (closed form)"); near(St.signal[41], 0.2 * mt(41) + 0.8 * (0.2 * mt(40)), 1e-12, "Signal recursion one bar later");
near(St.hist[41], St.macd[41] - St.signal[41], 1e-12, "Histogram identity one bar later");
// signal seed is the SIMPLE average of the first 9 MACD values (step at index 30 so those values differ)
const step30 = Array(30).fill(100).concat(Array(30).fill(110)), S30 = S.macd(step30), m30 = (t) => 10 * (Math.pow(25 / 27, t - 29) - Math.pow(11 / 13, t - 29));
near(S30.signal[33], (m30(30) + m30(31) + m30(32) + m30(33)) / 9, 1e-12, "Signal seed = SMA of MACD[25..33] (zeros before the step count)");
near(S30.signal[34], 0.2 * m30(34) + 0.8 * S30.signal[33], 1e-12, "Signal after the seed = EMA recursion with k = 0.2");

// ---------- 4. warm-up / insufficient history (exact counts) ----------
const w = (n) => S.macd(walkCloses(n, 100, 3));
eq([nn(w(25).macd), nn(w(25).signal), nn(w(25).hist)], [0, 0, 0], "25 candles: no MACD at all");
eq([nn(w(26).macd), nn(w(26).signal), nn(w(26).hist)], [1, 0, 0], "26 candles: MACD line only (1 value)");
eq([nn(w(33).macd), nn(w(33).signal), nn(w(33).hist)], [8, 0, 0], "33 candles: MACD line only (8 values), no Signal");
eq([nn(w(34).macd), nn(w(34).signal), nn(w(34).hist)], [9, 1, 1], "34 candles: MACD + Signal + Histogram");
eq(S.macd([]).macd, [], "empty input"); ok(!w(200).macd.some((v) => v !== null && !isFinite(v)) && !w(200).signal.some((v) => v !== null && !isFinite(v)), "no NaN or Infinity anywhere");
{ const r = w(200); let id = true; for (let i = 0; i < 200; i++) if (r.hist[i] !== null && Math.abs(r.hist[i] - (r.macd[i] - r.signal[i])) > 1e-12) id = false; ok(id, "Histogram = MACD - Signal at every defined index"); }
eq(S.macd(walkCloses(200, 100, 3)), S.macd(walkCloses(200, 100, 3)), "deterministic: same input, identical output");

// ---------- 5. model: same dates as the price chart, full history first ----------
const closes400 = walkCloses(400, 3500, 11), cs400 = candlesFor(closes400), doc400 = docOf(cs400);
for (const tf of ["1M", "3M", "6M", "1Y"]) {
  const m = S.prepare(doc400, "TCS", tf), t = (a) => a.map((p) => p.time);
  eq(t(m.macd.line), t(m.candles), tf + ": MACD line dates = price chart dates"); eq(t(m.macd.signal), t(m.candles), tf + ": Signal dates = price chart dates"); eq(t(m.macd.hist), t(m.candles), tf + ": Histogram dates = price chart dates");
}
const full = S.macd(closes400), m1m = S.prepare(doc400, "TCS", "1M"), idx1m = cs400.findIndex((c) => c.date === m1m.candles[0].time);
ok(m1m.avail.macd && m1m.avail.macdSignal, "1M window (~21 candles) still has MACD, Signal and Histogram"); near(m1m.macd.line[0].value, full.macd[idx1m], 1e-12, "1M window's first MACD value = value from the FULL series");
near(m1m.macd.signal[0].value, full.signal[idx1m], 1e-12, "1M window's first Signal value = value from the FULL series"); near(m1m.macd.hist[0].value, full.hist[idx1m], 1e-12, "1M window's first Histogram value = value from the FULL series");
eq(nn(S.macd(closes400.slice(idx1m)).macd.slice(0, 20)), 0, "...whereas MACD computed on the sliced closes alone would still be empty at that point (warm-up proves full history is used)");
eq(m1m.macd.hist.filter((p) => p.value !== undefined).length, m1m.candles.length, "1M: every bar has a histogram value");
// 3Y / 5Y
const closes1305 = walkCloses(1305, 3000, 5), doc5 = docOf(candlesFor(closes1305, "2021-10-04")), long = S.prepare(doc5, "TCS", "5Y");
ok(long.tfs.includes("3Y") && long.tfs.includes("5Y"), "3Y and 5Y available with 5 years of data");
for (const tf of ["3Y", "5Y"]) { const m = S.prepare(doc5, "TCS", tf); eq(m.macd.line.map((p) => p.time), m.candles.map((p) => p.time), tf + ": MACD dates = price chart dates"); }
ok(S.prepare(doc400, "TCS", "5Y").tf === "1Y", "5Y not offered with ~1.6 years of data (falls back)");
// 30..33 candles: line only; below 30 the chart itself refuses (existing rule)
const few = (n) => S.prepare(docOf(candlesFor(walkCloses(n, 100, 9))), "TCS", "1Y");
ok(few(30).avail.macd && !few(30).avail.macdSignal, "30 candles: MACD line, no Signal"); ok(few(33).avail.macd && !few(33).avail.macdSignal, "33 candles: MACD line, no Signal"); ok(few(34).avail.macdSignal, "34 candles: Signal available");
eq(few(29).error, "Not enough historical data.", "29 candles: the chart shows Not enough historical data.");
// missing candles: a gap makes the series shorter, never interpolated
const gapCs = cs400.filter((_, i) => ![100, 101, 102, 250, 251].includes(i)), mg = S.prepare(docOf(gapCs), "TCS", "1Y"), fg = S.macd(gapCs.map((c) => c.close));
eq(mg.macd.line.length, mg.candles.length, "missing candles: one MACD point per remaining candle (no filling)"); near(mg.macd.line[mg.macd.line.length - 1].value, fg.macd[fg.macd.length - 1], 1e-12, "missing candles: values computed over the candles that exist");
ok(Math.abs(mg.macd.line[mg.macd.line.length - 1].value - m1m.macd.line[m1m.macd.line.length - 1].value) > 1e-9, "removing candles changes the series (nothing is interpolated back in)");
// duplicates / invalid rows / shuffled input
const dupCs = cs400.concat([{ ...cs400[200], close: cs400[200].close + 1, high: cs400[200].high + 1 }]), md = S.prepare(docOf(dupCs), "TCS", "1Y");
const dedup = cs400.slice(); dedup[200] = dupCs[400]; eq(md.macd.line, S.prepare(docOf(dedup), "TCS", "1Y").macd.line, "duplicate date: the later row wins, one candle per date");
const dirty = cs400.concat([{ date: "bad", open: 1, high: 1, low: 1, close: 1 }, { date: "2030-01-01", open: 0, high: 1, low: 0, close: 0 }, { date: "2030-01-02", open: 1, high: 0.5, low: 2, close: 1 }, { date: "2030-01-03", open: NaN, high: 1, low: 1, close: 1 }, null]);
eq(S.prepare(docOf(dirty), "TCS", "1Y").macd, m1y(), "invalid rows are dropped: identical MACD to the clean data"); function m1y() { return S.prepare(doc400, "TCS", "1Y").macd; }
eq(S.prepare(docOf(cs400.slice().reverse()), "TCS", "1Y").macd, m1y(), "shuffled / reversed input gives the same MACD"); eq(S.prepare(doc400, "TCS", "1Y").macd, m1y(), "deterministic: preparing twice gives identical output");

// ---------- 6. the page: panel, placement, redraw, sync ----------
function stubLib(log) {
  const mkSeries = (kind, opts) => { const s = { kind, opts, data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; return s; };
  return { createChart(el, opts) { const ch = { el, opts, series: [], removed: false, handlers: [], ranges: [],
    addCandlestickSeries(o) { const s = mkSeries("candle", o); ch.series.push(s); return s; }, addLineSeries(o) { const s = mkSeries("line", o); ch.series.push(s); return s; }, addHistogramSeries(o) { const s = mkSeries("hist", o); ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange(f) { ch.handlers.push(f); }, setVisibleLogicalRange(r) { ch.ranges.push(r); } }; }, remove() { ch.removed = true; } }; log.push(ch); return ch; } };
}
async function boot(hash, doc) {
  const els = {}, win = {}, charts = [];
  const mk = (id) => { const e = { id, innerHTML: "", textContent: "", attrs: {}, listeners: {}, setAttribute(k, v) { e.attrs[k] = v; }, addEventListener(t, f) { e.listeners[t] = f; }, querySelector: () => null }; return e; };
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {}, LightweightCharts: stubLib(charts) };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => els[i] || (els[i] = mk(i)), addEventListener() {}, createElement: () => ({}), head: { appendChild() {} } };
  const fetched = []; global.fetch = async (u) => { fetched.push(u); const f = u.endsWith("historical.json") ? doc : null; return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(chartCode); eval(detailCode); await new Promise((r) => setTimeout(r, 40));
  return { els, charts, fetched, box: () => els.detailChart, click: async (tf) => { els.detailChart.listeners.click({ type: "click", target: { getAttribute: (a) => (a === "data-tf" ? tf : null) } }); await new Promise((r) => setTimeout(r, 30)); } };
}
const last = (t, id) => t.charts.filter((c) => c.el.id === id).pop();
(async () => {
  let t = await boot("#stock=TCS", doc400), h = t.box().innerHTML;
  re(h, /<b>MACD \(12, 26, 9\)<\/b>/, "panel labelled MACD (12, 26, 9)"); ok(!!last(t, "slMacd"), "MACD chart panel is created");
  const pos = (s) => h.indexOf(s); ok(pos('id="slRsi"') > 0 && pos('id="slMacd"') > pos('id="slRsi"'), "MACD panel is below the RSI panel"); ok(pos('id="slMacd"') < pos("Relative Strength vs Nifty 50") && pos("Relative Strength vs Nifty 50") < pos('id="slRs"'), "MACD panel is before Relative Strength vs Nifty 50");
  const mp = last(t, "slMacd"), kinds = mp.series.map((s) => s.kind + ":" + (s.opts.title || ""));
  eq(kinds, ["hist:Histogram", "line:MACD", "line:Signal"], "MACD panel has histogram, MACD line and Signal line"); const line = mp.series[1];
  ok(line.lines.length === 1 && line.lines[0].price === 0, "zero reference line at 0"); eq(line.data.length, last(t, "slPrice").series[0].data.length, "MACD line has one point per price candle");
  eq(line.data.map((p) => p.time), last(t, "slPrice").series[0].data.map((p) => p.time), "MACD dates are the price chart's dates");
  eq(mp.series[0].data.map((p) => p.time), line.data.map((p) => p.time), "Histogram and MACD line share dates"); eq(mp.series[2].data.map((p) => p.time), line.data.map((p) => p.time), "Signal and MACD line share dates");
  const cols = new Set(mp.series[0].data.filter((p) => p.color).map((p) => p.color)); ok(cols.size === 2 && [...cols].every((c) => /^rgba\(/.test(c)), "histogram uses two neutral shades");
  no([...cols].concat(mp.series.map((s) => s.opts.color || "")).join(" "), /#0E8F5E|#D13B3B|#3DD598|#FF6B6B|green|red/i, "no green/red buy-sell colouring in the MACD panel");
  near(line.data[line.data.length - 1].value, S.macd(closes400).macd[399], 1e-12, "page shows the full-history MACD value for the last bar"); near(mp.series[2].data[mp.series[2].data.length - 1].value, S.macd(closes400).signal[399], 1e-12, "page Signal value");
  // synchronized zoom/pan: every panel including MACD subscribes, and a range change on one reaches MACD
  const price = last(t, "slPrice"); ok(price.handlers.length > 0 && mp.handlers.length > 0, "price and MACD panels both subscribe to range changes");
  const before = mp.ranges.length; price.handlers[0]({ from: 5, to: 50 }); eq(mp.ranges[mp.ranges.length - 1], { from: 5, to: 50 }, "zooming/panning the price chart moves the MACD panel"); ok(mp.ranges.length === before + 1, "...exactly once (no loop)");
  const before2 = price.ranges.length; mp.handlers[0]({ from: 7, to: 30 }); eq(price.ranges[price.ranges.length - 1], { from: 7, to: 30 }, "zooming/panning the MACD panel moves the price chart"); ok(price.ranges.length === before2 + 1, "...exactly once (no loop)");
  const rs = last(t, "slRs"), rsi = last(t, "slRsi"); mp.handlers[0]({ from: 1, to: 9 }); eq([rs.ranges[rs.ranges.length - 1], rsi.ranges[rsi.ranges.length - 1]], [{ from: 1, to: 9 }, { from: 1, to: 9 }], "MACD, RSI and Relative Strength stay on one range");
  // wording + other panels untouched
  const macdHtml = h.slice(h.indexOf("<b>MACD (12, 26, 9)</b>"), h.indexOf("<b>Relative Strength vs Nifty 50</b>")), text = macdHtml.replace(/<[^>]+>/g, " ").replace(/&mdash;/g, "-");
  re(text, /Descriptive only, not a recommendation\./, "descriptive-only wording"); no(text, /\b(bullish|bearish|buy|sell|crossover|golden cross|death cross|oversold|overbought)\b/i, "no buy/sell/bullish/bearish/crossover wording in the MACD panel");
  no(macdHtml + chartCode.slice(chartCode.indexOf("function macd("), chartCode.indexOf("/* ---------- model for one stock")), /\b(bullish|bearish|buy|sell|crossover)\b/i, "no such wording in the MACD code or note");
  ok(["20 DMA", "50 DMA", "200 DMA"].every((x) => price.series.some((s) => s.opts.title === x)), "DMAs still drawn"); ok(rsi.series[0].lines.map((l) => l.price).join() === "70,50,30", "RSI levels unchanged"); re(h, /VWAP &mdash; Available in a future intraday-data phase/, "VWAP still deferred"); ok(!!rs, "Relative Strength panel still drawn");
  // redraw / timeframe changes
  const n1 = t.charts.length; await t.click("1M"); const m2 = last(t, "slMacd"), p2 = last(t, "slPrice"); ok(t.charts.length > n1 && m2 !== mp && mp.removed, "timeframe change redraws and removes the old MACD chart");
  eq(m2.series[1].data.map((p) => p.time), p2.series[0].data.map((p) => p.time), "1M: MACD dates = price dates"); ok(m2.series[1].data.length >= 20 && m2.series[1].data.length <= 23, "1M: ~21 MACD points"); ok(m2.series[1].data[0].value !== undefined, "1M: MACD already defined at the first bar (full-history warm-up)");
  await t.click("6M"); eq(last(t, "slMacd").series[1].data.length, last(t, "slPrice").series[0].data.length, "6M: MACD length = price length"); eq(t.fetched.filter((u) => u.includes("historical")).length, 2, "timeframe changes make no new request (the chart and the Stock Detail price tiles each fetched once on open)");
  // 5Y page
  t = await boot("#stock=TCS", doc5); await t.click("5Y"); eq(last(t, "slMacd").series[1].data.length, last(t, "slPrice").series[0].data.length, "5Y: MACD length = price length"); await t.click("3Y"); eq(last(t, "slMacd").series[1].data.length, last(t, "slPrice").series[0].data.length, "3Y: MACD length = price length");
  // short histories on the page
  t = await boot("#stock=TCS", docOf(candlesFor(walkCloses(31, 100, 4)))); ok(!!last(t, "slMacd"), "31 candles: MACD panel drawn"); eq(last(t, "slMacd").series.map((s) => s.opts.title), ["Histogram", "MACD"], "31 candles: no Signal series (never zero-filled)"); re(t.box().innerHTML, /Signal line and histogram need at least 34 candles\./, "31 candles: explanatory note");
  ok(last(t, "slMacd").series[0].data.every((p) => p.value === undefined), "31 candles: histogram has no values");
  t = await boot("#stock=TCS", docOf(candlesFor(walkCloses(20, 100, 4)))); eq(t.box().innerHTML, "Not enough historical data.", "20 candles: Not enough historical data."); eq(t.charts.length, 0, "20 candles: no panels");
  t = await boot("#stock=TCS", null); eq(t.box().innerHTML, "Historical data unavailable.", "missing file: Historical data unavailable."); t = await boot("#stock=ZZZ", doc400); eq(t.box().innerHTML, "Not enough historical data.", "unknown symbol: message, no crash");
  // routing: each stock gets its own MACD
  const two = JSON.parse(JSON.stringify(doc400)); two.stocks.INFY = { symbol: "INFY", candles: candlesFor(walkCloses(400, 1500, 77)) };
  t = await boot("#stock=INFY", two); near(last(t, "slMacd").series[1].data.slice(-1)[0].value, S.macd(walkCloses(400, 1500, 77)).macd[399], 1e-12, "INFY page shows INFY's MACD"); t = await boot("#stock=TCS", two); near(last(t, "slMacd").series[1].data.slice(-1)[0].value, S.macd(closes400).macd[399], 1e-12, "TCS page shows TCS's MACD");

  // ---------- security / scope / untouched blocks ----------
  no(html, /UPSTOX_ANALYTICS_TOKEN|Bearer\s|Authorization/i, "no token or auth header in the page"); no(html, /api\.upstox\.com/, "no Upstox call in the browser"); no(chartCode, /WebSocket|EventSource|setInterval|XMLHttpRequest/, "no live data in the chart module");
  eq((chartCode.match(/fetch\(/g) || []).length, 1, "the chart module still makes exactly one fetch"); re(chartCode, /fetch\("out\/historical\.json"/, "...of out/historical.json");
  eq(sha(techCode), "5f187e38d73cb15eed203fbc0cc41deb7a88dce219c6eee6f55b6f99e719b339", "Technical Snapshot block unchanged (byte-identical)"); eq(sha(PINDETAIL), "7db88e5847e49f38fb49a260ca6a6818e1610f4c3f09e783702fd86233e7ad10", "Stock Detail block is the Phase 5D.1 version (byte-identical to it)");
  eq(sha(PINBLOCKS[PINBLOCKS.length - 1]), "35de0d215ec3947da870f95e636f41bf4b130d6d929be4d91d3deb2661344d2f", "final Phase 2B block unchanged (byte-identical)");
  console.log("MACD tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
