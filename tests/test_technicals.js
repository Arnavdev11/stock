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
eq(T.atr(flat14().slice(0, 13), 14), null, "ATR needs 14 candles"); e
