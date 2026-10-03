// Run: node tests/test_chart.js   (no network, no browser; stubs the DOM, fetch and the TradingView library)
// Tests the Phase 3 Step 2 chart: indicator maths against hand-calculated values, data handling, routing, error states.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart"));
const detailCode = blocks.find((b) => b.includes("Stock Detail view"));
assert.ok(chartCode && detailCode, "chart and detail script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      near = (a, b, tol, m) => { checks++; assert.ok(Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };

// ---------- synthetic history (tests only; never shipped as data) ----------
function weekdays(n, start) { const out = [], d = new Date(start + "T00:00:00Z"); while (out.length < n) { if (d.getUTCDay() % 6) out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 1); } return out; }
function walk(n, p0, seed, start = "2023-01-02") {
  let s = seed, p = p0; const rnd = () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296;
  return weekdays(n, start).map((date) => { const o = p, c = p * (1 + (rnd() - 0.48) * 0.03), h = Math.max(o, c) * 1.005, l = Math.min(o, c) * 0.995;
    p = c; return { date, open: +o.toFixed(2), high: +h.toFixed(2), low: +l.toFixed(2), close: +c.toFixed(2), volume: 100000 + Math.floor(rnd() * 50000) }; });
}
const DAYS = 400;
const HIST = { updated: "2026-10-01", source: "Upstox", interval: "1day",
  benchmark: { symbol: "NIFTY 50", candles: walk(DAYS, 20000, 7).map((c) => ({ ...c, volume: 0 })) },
  stocks: { TCS: { symbol: "TCS", candles: walk(DAYS, 3500, 11) }, INFY: { symbol: "INFY", candles: walk(DAYS, 1500, 23) },
            TINY: { symbol: "TINY", candles: walk(10, 100, 5) } } };
const C = (cs) => cs.map((c) => c.close);
const bare = (closes) => closes.map((c) => ({ date: "2024-01-01", open: c, high: c, low: c, close: c }));

// ---------- stubs ----------
function stubLib(log) {
  const mkSeries = (kind, opts) => { const s = { kind, opts, data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o.price); } }; return s; };
  return { createChart(el, opts) { const ch = { el, opts, series: [], removed: false,
    addCandlestickSeries(o) { const s = mkSeries("candle", o); ch.series.push(s); return s; },
    addLineSeries(o) { const s = mkSeries("line", o); ch.series.push(s); return s; },
    addHistogramSeries(o) { const s = mkSeries("hist", o); ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }; },
    remove() { ch.removed = true; } }; log.push(ch); return ch; } };
}
async function boot(hash, files = { "historical.json": HIST }, { lib = true } = {}) {
  const els = {}, win = {}, charts = [], fetched = [], injected = [];
  const mk = (id) => { const e = { id, innerHTML: "", textContent: "", attrs: {}, listeners: {}, setAttribute(k, v) { e.attrs[k] = v; },
    addEventListener(t, f) { e.listeners[t] = f; }, querySelector: () => null }; return e; };
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  if (lib === true) window.LightweightCharts = stubLib(charts);
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } },
    getElementById: (i) => els[i] || (els[i] = mk(i)), addEventListener() {},
    createElement: () => ({}), head: { appendChild(el) { injected.push(el.src); setTimeout(() => { if (lib === "inject") { window.LightweightCharts = stubLib(charts); el.onload(); } else el.onerror(); }, 0); } } };
  global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()]; return { ok: f !== undefined && f !== null, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(chartCode); eval(detailCode);
  await new Promise((r) => setTimeout(r, 40));
  const nav = async (h) => { window.location.hash = h; win.hashchange(); await new Promise((r) => setTimeout(r, 40)); };
  return { els, charts, fetched, injected, nav, win, detail: () => els.detail.innerHTML, box: () => els.detailChart };
}
const priceChart = (t) => t.charts.filter((c) => c.el.id === "slPrice").pop();
const candleData = (t) => priceChart(t).series.find((s) => s.kind === "candle").data;
const titles = (t) => t.charts.flatMap((c) => c.series.map((s) => s.opts.title || s.kind));

(async () => {
  // 1. historical JSON structure (what historical_updater.py writes and the page reads)
  ok(HIST.updated && HIST.stocks.TCS.candles.length === DAYS, "doc has updated + stocks.TCS.candles");
  for (const k of ["date", "open", "high", "low", "close", "volume"]) ok(k in HIST.stocks.TCS.candles[0], "candle has " + k);
  ok(fs.existsSync(__dirname + "/../historical_updater.py"), "historical_updater.py exists");
  const SC = new Function(chartCode.replace('"use strict";', "") .replace("(function(){", "var window={};(function(){").replace(/\}\)\(\);\s*$/, "})();return window.SLChart;"));
  global.window = { }; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
  const S = SC();
  ok(S && typeof S.sma === "function", "indicator API exposed");

  // cleaning: bad rows dropped, nothing invented
  const dirty = S.cleanCandles([{ date: "2024-01-02", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }, { date: "2024-01-03", open: 1, high: 0.5, low: 2, close: 1, volume: 1 },
    { date: "bad", open: 1, high: 1, low: 1, close: 1 }, { date: "2024-01-04", open: 0, high: 1, low: 1, close: 1 }, { date: "2024-01-05", open: 1, high: 2, low: 1, close: 2, volume: null },
    { date: "2024-01-02", open: 9, high: 9, low: 9, close: 9, volume: 1 }, null, { date: "2024-01-06", open: NaN, high: 1, low: 1, close: 1 }]);
  eq(dirty.map((c) => c.date), ["2024-01-02", "2024-01-05"], "invalid, zero-price, duplicate and NaN rows dropped; sorted");
  eq(dirty[0].close, 9, "duplicate date keeps the later row"); eq(dirty[1].volume, null, "missing volume stays null (not invented)");

  // 5-7. SMA / DMA
  const ramp = Array.from({ length: 30 }, (_, i) => i + 1);
  const m20 = S.sma(ramp, 20); eq(m20[18], null, "20 DMA empty before 20 closes"); near(m20[19], 10.5, 1e-9, "20 DMA at day 20 = mean(1..20)"); near(m20[29], 20.5, 1e-9, "20 DMA at day 30 = mean(11..30)");
  near(S.sma(ramp, 5)[4], 3, 1e-9, "5-period SMA"); eq(S.sma(ramp, 50).every((v) => v === null), true, "50 DMA null with 30 closes");
  const r250 = Array.from({ length: 250 }, (_, i) => i + 1); near(S.sma(r250, 50)[249], 225.5, 1e-9, "50 DMA = mean(201..250)"); near(S.sma(r250, 200)[249], 150.5, 1e-9, "200 DMA = mean(51..250)"); eq(S.sma(r250, 200)[198], null, "200 DMA empty before 200 closes");
  // 8-9. EMA (is not the SMA)
  const e3 = S.ema([1, 2, 3, 4, 5], 3); eq(e3.slice(0, 2), [null, null], "EMA empty before seed"); eq(e3.slice(2), [2, 3, 4], "EMA(3): seed = SMA, k = 0.5");
  const e20 = S.ema(r250, 20), s20 = S.sma(r250, 20); near(e20[19], 10.5, 1e-9, "EMA20 seeded with SMA20"); const sq = r250.map((x) => x * x); ok(Math.abs(S.ema(sq, 20)[249] - S.sma(sq, 20)[249]) > 30, "EMA is not the same as DMA (on a curved series they differ)");
  near(e20[249], 250 - 9.5, 1e-9, "EMA20 of a straight ramp lags by (n-1)/2 = 9.5"); near(S.ema(r250, 50)[249], 250 - 24.5, 1e-9, "EMA50 of a straight ramp lags by 24.5");
  // 10. RSI(14)
  const ST = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64];
  const rs = S.rsi(ST, 14); eq(rs.slice(0, 14).every((v) => v === null), true, "RSI empty for the first 14 closes"); near(rs[14], 70.53, 0.1, "RSI(14) matches the classic worked example (first value)");
  near(rs[15], 66.32, 0.1, "RSI(14) second value (Wilder smoothing)"); eq(S.rsi(ramp, 14)[29], 100, "RSI 100 when there are no losses"); eq(S.rsi(Array(20).fill(5), 14)[19], 50, "RSI 50 when nothing moves");
  eq(S.rsi(ramp.slice().reverse(), 14)[29], 0, "RSI 0 when there are no gains");
  // 11. Supertrend (ATR 10, multiplier 3)
  eq(S.PARAMS.supertrendPeriod, 10, "Supertrend ATR period 10"); eq(S.PARAMS.supertrendMultiplier, 3, "Supertrend multiplier 3");
  const flat = Array.from({ length: 15 }, () => ({ high: 11, low: 9, close: 10 })); const sf = S.supertrend(flat, 10, 3);
  eq(sf.slice(0, 9).every((v) => v === null), true, "Supertrend empty until ATR exists"); eq(sf[9].value, 16, "flat TR=2 -> ATR 2 -> upper band 10 + 3*2 = 16"); eq(sf[9].dir, -1, "close below upper band -> down-trend");
  // hand-calculated ATR smoothing: ten TR=2 bars, then TR=12 -> ATR=(2*9+12)/10=3 -> lower band 14-3*3=5; then TR=4 -> ATR=(3*9+4)/10=3.1 -> lower band 18-9.3=8.7
  const hc = flat.slice(0, 10).concat([{ high: 20, low: 8, close: 18 }, { high: 20, low: 16, close: 19 }]); const sh = S.supertrend(hc, 10, 3);
  eq(sh[10].dir, 1, "close above the upper band flips to up-trend"); near(sh[10].value, 5, 1e-9, "Wilder ATR smoothing: ATR 3.0 -> band 5"); near(sh[11].value, 8.7, 1e-9, "Wilder ATR smoothing: ATR 3.1 -> band 8.7");
  // gap down: TR = max(H-L, |H-prevC|, |L-prevC|) = max(2, 2, 4) = 4 -> ATR 2.2 -> upper band 7 + 6.6 = 13.6 (tightens from 16)
  const gp = S.supertrend(flat.slice(0, 10).concat([{ high: 8, low: 6, close: 7 }]), 10, 3); near(gp[10].value, 13.6, 1e-9, "true range uses the gap to the previous close (|L - prevC|)"); eq(gp[10].dir, -1, "gap down stays in down-trend");
  // gap up: TR = max(2, |H-prevC|=8, |L-prevC|=6) = 8 -> ATR 2.6 -> lower band 17 - 7.8 = 9.2, close 17 is above the upper band 16 -> up-trend
  const gu = S.supertrend(flat.slice(0, 10).concat([{ high: 18, low: 16, close: 17 }]), 10, 3); near(gu[10].value, 9.2, 1e-9, "true range uses the gap up (|H - prevC|)"); eq(gu[10].dir, 1, "gap up flips to up-trend");
  const up = Array.from({ length: 40 }, (_, i) => ({ high: 11 + i * 2, low: 9 + i * 2, close: 10.5 + i * 2 })); const su = S.supertrend(up, 10, 3);
  ok(su[39].dir === 1 && su[39].value < up[39].close, "steady rise -> up-trend, line below price");
  for (let i = 12; i < 40; i++) ok(su[i].value >= su[i - 1].value || su[i].dir !== su[i - 1].dir, "up-trend line never moves down");
  const crash = up.concat(Array.from({ length: 12 }, (_, i) => ({ high: 70 - i * 6, low: 66 - i * 6, close: 67 - i * 6 }))); const sc = S.supertrend(crash, 10, 3);
  ok(sc[sc.length - 1].dir === -1 && sc[sc.length - 1].value > crash[crash.length - 1].close, "sharp fall flips to down-trend, line above price");
  // 12. Relative strength vs Nifty
  const sk = [100, 150, 200].map((c, i) => ({ date: "2024-01-0" + (i + 1), close: c })), bk = [1000, 1250, 1500].map((c, i) => ({ date: "2024-01-0" + (i + 1), close: c }));
  const rr = S.relStrength(sk, bk); near(rr.last, 200 / 150 * 100, 1e-9, "RS = (stock +100%)/(Nifty +50%) x 100"); near(rr.stockReturn, 100, 1e-9, "stock return"); near(rr.benchReturn, 50, 1e-9, "Nifty return"); near(rr.points[0].value, 100, 1e-9, "RS starts at 100");
  eq(S.relStrength(sk, [{ date: "2023-01-01", close: 5 }]), null, "no common dates -> null (no score invented)"); eq(S.relStrength(sk, null), null, "no benchmark -> null");
  const gap = S.relStrength(sk, bk.slice(1)); eq(gap.points.length, 2, "only dates present in both series are compared"); near(gap.last, (200 / 150) / (1500 / 1250) * 100, 1e-9, "RS rebased to first common date");

  // 3-4. candle + volume mapping through the model
  const doc = JSON.parse(JSON.stringify(HIST)), mdl = S.prepare(doc, "TCS", "1Y"), all = doc.stocks.TCS.candles;
  eq(mdl.candles[mdl.candles.length - 1], { time: all[DAYS - 1].date, open: all[DAYS - 1].open, high: all[DAYS - 1].high, low: all[DAYS - 1].low, close: all[DAYS - 1].close }, "last candle maps O/H/L/C exactly");
  eq(mdl.volume[mdl.volume.length - 1].value, all[DAYS - 1].volume, "volume maps exactly"); eq(mdl.volume.length, mdl.candles.length, "one volume bar per candle");
  const noVol = JSON.parse(JSON.stringify(HIST)); noVol.stocks.TCS.candles[DAYS - 1].volume = null; eq(S.prepare(noVol, "TCS", "1Y").volume.length, S.prepare(doc, "TCS", "1Y").volume.length - 1, "missing volume -> no bar (not zero)");
  // indicators in the model are the full-history values, cut to the range
  const L = (k) => mdl.lines[k][mdl.lines[k].length - 1].value;
  near(L("dma20"), C(all).slice(-20).reduce((a, b) => a + b) / 20, 1e-9, "model 20 DMA = mean of last 20 closes"); near(L("dma50"), C(all).slice(-50).reduce((a, b) => a + b) / 50, 1e-9, "model 50 DMA");
  near(L("dma200"), C(all).slice(-200).reduce((a, b) => a + b) / 200, 1e-9, "model 200 DMA"); near(L("ema20"), S.ema(C(all), 20)[DAYS - 1], 1e-9, "model 20 EMA"); near(L("ema50"), S.ema(C(all), 50)[DAYS - 1], 1e-9, "model 50 EMA");
  near(mdl.rsi[mdl.rsi.length - 1].value, S.rsi(C(all), 14)[DAYS - 1], 1e-9, "model RSI(14)"); ok(mdl.avail.st && mdl.avail.dma200 && mdl.avail.rsi, "indicators available with 400 days");
  ok(S.prepare(doc, "TCS", "1M").lines.dma200[0].value !== undefined, "200 DMA is already warm at the start of a 1M range (calculated on full history, not just the shown range)");
  // 18. timeframe filtering
  const n = (tf) => S.prepare(doc, "TCS", tf).candles.length;
  ok(n("1M") >= 20 && n("1M") <= 23, "1M ~ 21 trading days"); ok(n("3M") > n("1M") && n("6M") > n("3M") && n("1Y") > n("6M"), "ranges grow 1M < 3M < 6M < 1Y");
  eq(mdl.tfs, ["1M", "3M", "6M", "1Y"], "3Y/5Y hidden when the data does not reach back that far");
  const big = JSON.parse(JSON.stringify(HIST)); big.stocks.TCS.candles = walk(1100, 3000, 3, "2021-06-01"); const mb = S.prepare(big, "TCS", "3Y");
  ok(mb.tfs.includes("3Y") && !mb.tfs.includes("5Y"), "3Y offered with ~4 years of data, 5Y not until it is really there"); ok(mb.tf === "3Y" && mb.candles.length > 700, "3Y range selected");
  eq(S.prepare(doc, "TCS", "5Y").tf, "1Y", "unavailable timeframe falls back to 1Y");
  // Nifty benchmark is really used: scaling Nifty changes nothing (ratios are rebased); using the stock itself as benchmark gives exactly 100
  const b2 = JSON.parse(JSON.stringify(HIST)); b2.benchmark.candles.forEach((c) => { c.close = +(c.close * 2).toFixed(6); });
  const rsA = S.prepare(doc, "TCS", "1Y").rs, rsB = S.prepare(b2, "TCS", "1Y").rs;
  near(rsB.last, rsA.last, 1e-3, "RS is unchanged when every Nifty close is doubled (rebased ratio)");
  const b3 = JSON.parse(JSON.stringify(HIST)); b3.benchmark.candles = JSON.parse(JSON.stringify(b3.stocks.TCS.candles)); const rsC = S.prepare(b3, "TCS", "1Y").rs;
  near(rsC.last, 100, 1e-9, "stock vs itself = 100"); near(rsC.benchReturn, rsC.stockReturn, 1e-9, "benchmark return is taken from historical.benchmark");
  ok(Math.abs(rsA.benchReturn - rsA.stockReturn) > 0.01, "with the real Nifty series the two returns differ");
  // real-size dataset (the live file: ~5 years of daily candles): 3Y and 5Y are offered, 200 DMA is warm
  const real = JSON.parse(JSON.stringify(HIST)); real.stocks.TCS.candles = walk(1305, 3500, 13, "2021-10-04"); real.benchmark.candles = walk(1305, 20000, 7, "2021-10-04");
  const mr = S.prepare(real, "TCS", "5Y"); ok(mr.tfs.includes("3Y") && mr.tfs.includes("5Y") && mr.tf === "5Y", "3Y and 5Y offered when the daily candles cover 5 years (as the live file does)"); ok(mr.candles.length > 1200 && mr.rs && mr.avail.dma200, "5Y range shows the full history with indicators");
  // 13/14/15. missing / insufficient / invalid symbol (model level)
  eq(S.prepare(doc, "TINY", "1Y").error, "Not enough historical data.", "insufficient history"); eq(S.prepare(doc, "ZZZ", "1Y").error, "Not enough historical data.", "unknown symbol");
  eq(S.prepare({}, "TCS", "1Y").error, "Not enough historical data.", "empty document"); eq(S.prepare(null, "TCS", "1Y").error, "Not enough historical data.", "null document");
  const few = JSON.parse(JSON.stringify(HIST)); few.stocks.TCS.candles = walk(100, 3500, 11); const mf = S.prepare(few, "TCS", "1Y");
  eq(mf.avail.dma200, false, "200 DMA not available with 100 days (nothing invented)"); ok(mf.avail.dma50 && mf.avail.rsi, "shorter indicators still available");
  const nob = JSON.parse(JSON.stringify(HIST)); delete nob.benchmark; eq(S.prepare(nob, "TCS", "1Y").rs, null, "no Nifty data -> RS unavailable, chart still works");

  // ---------- page behaviour through the real Stock Detail script ----------
  // 2 + 16. TCS routing and data loading
  let t = await boot("#stock=TCS");
  ok(t.fetched.includes("out/historical.json"), "historical.json requested when a detail view opens");
  ok(t.fetched.filter((u) => u.includes("historical")).length === 1, "requested once");
  eq(candleData(t).length, n("1Y"), "TCS candles drawn"); eq(candleData(t)[candleData(t).length - 1].close, all[DAYS - 1].close, "TCS last close drawn");
  re(t.box().innerHTML, /data-tf="1Y"/, "timeframe controls rendered"); no(t.box().innerHTML, /Coming in Phase 3 Step 2/, "placeholder replaced by the chart");
  // 17. INFY routing: INFY data, not TCS
  await t.nav("#stock=INFY");
  eq(candleData(t)[candleData(t).length - 1].close, HIST.stocks.INFY.candles[DAYS - 1].close, "INFY last close drawn"); ok(candleData(t)[candleData(t).length - 1].close !== all[DAYS - 1].close, "INFY does not show TCS candles");
  // chart panels
  const ts = titles(t); ok(ts.includes("20 DMA") && ts.includes("50 DMA") && ts.includes("200 DMA"), "DMA lines drawn by default");
  ok(!ts.includes("20 EMA") && !ts.includes("Supertrend"), "EMA and Supertrend off until switched on");
  const rsiC = t.charts.filter((c) => c.el.id === "slRsi").pop(); eq(rsiC.series[0].lines, [70, 50, 30], "RSI reference levels 70 / 50 / 30"); re(t.box().innerHTML, /RSI \(14\)/, "RSI (14) label");
  ok(t.charts.some((c) => c.el.id === "slVol" && c.series[0].kind === "hist"), "volume histogram in its own panel below the price"); ok(t.charts.some((c) => c.el.id === "slRs"), "relative strength panel");
  re(t.box().innerHTML, /Relative Strength vs Nifty 50/, "RS label"); no(t.box().innerHTML, /\b(Strong )?(Buy|Sell)\b/, "no buy/sell wording on the chart");
  // controls: indicator toggles + timeframe, without refetching
  const fetchedBefore = t.fetched.length, ctl = (attr, v, type, extra = {}) => ({ type, target: { getAttribute: (a) => (a === attr ? v : null), ...extra } });
  t.box().listeners.change(ctl("data-ind", "ema20", "change", { checked: true })); await new Promise((r) => setTimeout(r, 20));
  ok(titles(t).includes("20 EMA"), "20 EMA switched on"); t.box().listeners.change(ctl("data-ind", "st", "change", { checked: true })); await new Promise((r) => setTimeout(r, 20));
  ok(titles(t).filter((x) => x === "Supertrend").length >= 2, "Supertrend drawn (up and down segments)"); t.box().listeners.change(ctl("data-ind", "dma50", "change", { checked: false })); await new Promise((r) => setTimeout(r, 20));
  const lastPrice = t.charts.filter((c) => c.el.id === "slPrice").pop(); ok(!lastPrice.series.some((s) => s.opts.title === "50 DMA"), "50 DMA switched off"); ok(lastPrice.series.some((s) => s.opts.title === "20 EMA"), "other indicators stay on");
  t.box().listeners.click(ctl("data-tf", "1M", "click")); await new Promise((r) => setTimeout(r, 20));
  const p1m = t.charts.filter((c) => c.el.id === "slPrice").pop(); ok(p1m.series[0].data.length >= 20 && p1m.series[0].data.length <= 23, "1M timeframe re-filters the loaded data");
  eq(t.fetched.length, fetchedBefore, "changing timeframe or indicators makes no new request"); ok(t.charts.filter((c) => c.removed).length > 0, "old charts are removed on redraw");
  // 19. VWAP is never calculated
  re(t.box().innerHTML, /VWAP &mdash; Available in a future intraday-data phase/, "VWAP placeholder text shown"); ok(!titles(t).some((x) => /vwap/i.test(x)), "no VWAP series drawn");
  no(chartCode.replace(/\/\*[\s\S]*?\*\//g, "").replace(/VWAP &mdash;[^<]*/g, ""), /vwap/i, "chart code never computes a VWAP"); ok(!("vwap" in mdl) && !("vwap" in mdl.lines), "no vwap in the model");
  // 20 + 13. placeholder only disappears when valid history loads
  t = await boot("#stock=TCS", { "historical.json": null });
  eq(t.box().innerHTML, "Historical data unavailable.", "missing historical.json -> message"); eq(t.charts.length, 0, "no charts without data");
  re(t.detail(), /Stock Detail: TCS/, "rest of the detail page still renders") ;
  t = await boot("#stock=TINY"); eq(t.box().innerHTML, "Not enough historical data.", "insufficient history message");
  t = await boot("#stock=ZZZ"); eq(t.box().innerHTML, "Not enough historical data.", "invalid symbol: message, no crash"); re(t.detail(), /Stock Detail: ZZZ/, "detail page for an unknown symbol still renders");
  t = await boot("#stock=TCS", { "historical.json": { updated: "x" } }); eq(t.box().innerHTML, "Historical data unavailable.", "malformed historical.json -> message");
  t = await boot("#stock=TCS", { "historical.json": HIST }, { lib: false }); eq(t.box().innerHTML, "Chart library could not be loaded.", "library failure is reported, page keeps working");
  t = await boot("#stock=TCS", { "historical.json": HIST }, { lib: "inject" });
  ok(t.charts.length > 0 && t.injected.length === 1 && /lightweight-charts@4\.2\.0/.test(t.injected[0]), "library loaded lazily (pinned version) only when a chart is needed");
  t = await boot("#stock=TCS"); eq(t.injected, [], "library not injected when already present");
  // placeholder is still in the Step 1 markup until the chart module replaces it
  re(blocks.find((b) => b.includes("Stock Detail view")), /Historical Price Chart — Coming in Phase 3 Step 2/, "Step 1 placeholder still rendered by the detail view");
  t = await boot("#stock=TCS", { "historical.json": HIST }); ok(!/Coming in Phase 3/.test(t.box().innerHTML), "placeholder text replaced only after valid data");
  // stale response: user left the page before the file arrived
  t = await boot(""); eq(t.charts.length, 0, "dashboard: no chart code runs"); ok(!t.fetched.some((u) => u.includes("historical")), "historical.json not fetched on the dashboard");
  // routing from Step 1 still works with both scripts present
  t = await boot("#stock=TCS"); re(t.detail(), /Stock Detail: TCS/, "Step 1 routing intact"); await t.nav("#stock=INFY"); re(t.detail(), /Stock Detail: INFY/, "Step 1 routing intact (INFY)");

  // ---------- security ----------
  const readme = [html, fs.existsSync(__dirname + "/../out/historical.json") ? fs.readFileSync(__dirname + "/../out/historical.json", "utf8") : ""];
  for (const s of readme) { no(s, /UPSTOX_ANALYTICS_TOKEN/, "token name absent from page / data"); no(s, /Bearer\s/i, "no auth header in page / data"); }
  no(html, /api\.upstox\.com/, "the browser never calls Upstox"); no(JSON.stringify(HIST), /token/i, "historical.json has no token");
  const py = fs.readFileSync(__dirname + "/../historical_updater.py", "utf8"); re(py, /get_token\(\)/, "updater reads the token from the environment only"); no(py, /print\([^)]*token/i, "updater never prints the token");
  // scope
  no(chartCode, /WebSocket|EventSource|setInterval|macd/i, "no live data, streaming or MACD"); no(html.split("<script>").slice(-3, -1).join(""), /WebSocket/, "no WebSocket anywhere in the new scripts");

  console.log("Chart tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
