"use strict";
// Run: node tests/test_indicator_readiness.js   (no network, no browser)
// coverage_report.py counts a stock's technical indicators as computable only when its saved candles meet INDICATOR_MIN_ROWS / INDICATOR_WINDOW_DAYS.
// Those numbers must be the page's own: this test runs the indicator functions of index.html (the price chart's sma, ema, rsi, supertrend, macd and
// MIN_CANDLES; the Technical Snapshot's atr, VOL_DAYS, RANGE_DAYS and DMA rule) and checks every number, so the report and the page cannot drift apart.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const py = fs.readFileSync(__dirname + "/../coverage_report.py", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); };

// --- the numbers coverage_report.py uses
const dictOf = (name) => { const m = new RegExp("^" + name + " = (\\{[\\s\\S]*?\\})\\n", "m").exec(py); ok(m, name + " found in coverage_report.py"); return JSON.parse(m[1].replace(/\s+/g, " ")); };
const MIN = dictOf("INDICATOR_MIN_ROWS"), WIN = dictOf("INDICATOR_WINDOW_DAYS");

// --- the page's own functions, taken from the two script blocks as they ship
const block = (marker) => { const i = html.indexOf(marker); ok(i > 0, marker + " block found"); return html.slice(html.lastIndexOf("<script>", i), html.indexOf("</script>", i)); };
const chart = block("Phase 3 Step 2 - historical price chart"), snap = block("Phase 3 Step 3 - Technical Snapshot for the Stock Detail view");
const fnSrc = (code, name) => { const i = code.indexOf("function " + name + "("); ok(i >= 0, name + " found"); const rest = code.slice(i + 1); const ends = ["\n/*", "\nfunction ", "\n\n"].map((t) => rest.indexOf(t)).filter((x) => x > 0); return code.slice(i, i + 1 + Math.min(...ends)); };
const constOf = (code, name) => { const m = new RegExp("\\b" + name + "=(\\d+)").exec(code); ok(m, name + " found"); return +m[1]; };
const K = { MIN_CANDLES: constOf(chart, "MIN_CANDLES"), RSI_PERIOD: constOf(chart, "RSI_PERIOD"), ST_PERIOD: constOf(chart, "ST_PERIOD"), ST_MULT: constOf(chart, "ST_MULT"),
            MACD_FAST: constOf(chart, "MACD_FAST"), MACD_SLOW: constOf(chart, "MACD_SLOW"), MACD_SIGNAL: constOf(chart, "MACD_SIGNAL"),
            ATR_PERIOD: constOf(snap, "ATR_PERIOD"), VOL_DAYS: constOf(snap, "VOL_DAYS"), RANGE_DAYS: constOf(snap, "RANGE_DAYS") };
const F = new Function("K", Object.keys(K).map((k) => "var " + k + "=K." + k + ";").join("") +
  ["sma", "ema", "rsi", "supertrend", "macd"].map((n) => fnSrc(chart, n)).join("\n") + "\nvar chartAtr=null;\n" +
  "return {sma:sma,ema:ema,rsi:rsi,supertrend:supertrend,macd:macd};")(K);
const atr = new Function(fnSrc(snap, "atr") + "\nreturn atr;")();

// synthetic candles: a rising, wiggling series (no flat stretch, so every definition produces a value as soon as it can)
const candles = (n) => Array.from({ length: n }, (_, i) => { const c = 100 + i + 3 * Math.sin(i); return { date: "d" + i, open: c, high: c + 2, low: c - 2, close: c, volume: 1000 + i }; });
const closes = (n) => candles(n).map((c) => c.close);
const start = (arr) => { const i = arr.findIndex((v) => v !== null && v !== undefined); return i < 0 ? null : i + 1; };   // rows needed for the first value
const N = 260;
const natural = {
  sma20: start(F.sma(closes(N), 20)), ema20: start(F.ema(closes(N), 20)), sma50: start(F.sma(closes(N), 50)), ema50: start(F.ema(closes(N), 50)),
  sma200: start(F.sma(closes(N), 200)), rsi14: start(F.rsi(closes(N), K.RSI_PERIOD)), supertrend_10_3: start(F.supertrend(candles(N), K.ST_PERIOD, K.ST_MULT)),
  macd_line: start(F.macd(closes(N)).macd), macd_signal: start(F.macd(closes(N)).signal) };
eq(natural, { sma20: 20, ema20: 20, sma50: 50, ema50: 50, sma200: 200, rsi14: 15, supertrend_10_3: 10, macd_line: 26, macd_signal: 34 },
   "each indicator's first value, from the page's own functions");

// the chart draws nothing below MIN_CANDLES, so the rows that SHOW an indicator = max(MIN_CANDLES, its own start)
eq(MIN.chart_drawn, K.MIN_CANDLES, "chart_drawn = the chart's MIN_CANDLES");
for (const [k, v] of Object.entries(natural)) eq(MIN[k], Math.max(K.MIN_CANDLES, v), k + " = max(MIN_CANDLES, its first value)");

// Technical Snapshot
eq(atr(candles(K.ATR_PERIOD - 1), K.ATR_PERIOD), null, "ATR 14 is not defined below 14 candles");
ok(typeof atr(candles(K.ATR_PERIOD), K.ATR_PERIOD) === "number", "ATR 14 is defined at 14 candles");
eq(MIN.snapshot_atr14, K.ATR_PERIOD, "snapshot_atr14 = the snapshot's ATR_PERIOD");
ok(/if\(n>VOL_DAYS&&last\.volume!=null\)/.test(snap), "the 20-day volume ratio needs more than VOL_DAYS candles");
eq(MIN.snapshot_volume_ratio_20d, K.VOL_DAYS + 1, "snapshot_volume_ratio_20d = VOL_DAYS + 1");
ok(/if\(n<k\)\{r\.dma\[k\]=null;return\}/.test(snap) && /\[20,50,200\]/.test(snap.replace(/\s/g, "")), "a snapshot DMA k needs k candles (20, 50, 200)");
eq(MIN.snapshot_dma200, 200, "snapshot_dma200 = 200");
ok(/addDays\(last\.date,-RANGE_DAYS\)/.test(snap) && /if\(cs\[0\]\.date<=ws\)/.test(snap), "the 52-week range needs history reaching RANGE_DAYS back");
eq(WIN, { snapshot_52_week_range: K.RANGE_DAYS }, "snapshot_52_week_range = RANGE_DAYS");

// every key the report counts is checked above, and nothing else is counted
eq(Object.keys(MIN).sort(), ["chart_drawn", ...Object.keys(natural), "snapshot_atr14", "snapshot_volume_ratio_20d", "snapshot_dma200"].sort(), "no unchecked threshold");

console.log("Indicator readiness tests passed (" + checks + " checks)");
