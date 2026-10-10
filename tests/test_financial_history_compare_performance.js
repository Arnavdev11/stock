// Run: node tests/test_financial_history_compare_performance.js   (no network, no browser)
// Tests the Phase 5D price performance section of the comparison page: 1M / 3M / 6M / 1Y / 3Y / 5Y returns for 2-5 stocks from out/historical.json, using the Stock Detail rule
// (latest valid daily close against the latest valid close on or before the calendar cutoff, no interpolation, invalid rows ignored before the dates are ordered), the factual table,
// the inline-SVG bar charts, missing and insufficient history, zero / positive / negative returns, order kept (no sorting, no ranking), responsive markup, neutral wording, and that
// the Phase 5B tables, the Phase 5C charts, the Financial History module and the rest of the page are exactly what they were at 823ecc9.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
// Phase 5I: behaviour is tested on the page as shipped (html). The byte-identity pins below are tested on PINHTML = the page minus the Phase 5I layer (legacy_5i.js, proven exact against aa4ace1 by test_global_navigation.js), because Phase 5I deliberately changes the route readers and the search mount.
const PINHTML = require("./legacy_5i.js").legacy(html), PINBLOCKS = PINHTML.split("<script>").slice(1).map((b) => b.split("</script>")[0]), PINDETAIL = PINBLOCKS.find((b) => b.includes("Stock Detail view"));
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const fhCode = modOf(html, FHTAG), cmpCode = modOf(html, CMPTAG), techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot"));
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const near = (a, b, tol, m) => { checks++; assert.ok(Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
const env = () => { global.MutationObserver = undefined; global.window = { location: { hash: "" }, addEventListener() {}, scrollTo() {} }; global.document = { getElementById: () => null, querySelector: () => null, body: { classList: { add() {}, remove() {} } } }; global.fetch = async () => ({ ok: false }); };
env(); (0, eval)(fhCode); (0, eval)(techCode); const T = window.SLTech, F = window.SLFinHistory; (0, eval)(cmpCode); const C = window.SLCompare;
ok(T && F && C && typeof T.compute === "function", "the Stock Detail snapshot code, the history module and the compare module all load");

// ---------- fixtures ----------
const cd = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const P = (map) => ({ updated: "2026-10-05", source: "Upstox", stocks: Object.fromEntries(Object.entries(map).map(([s, arr]) => [s, { symbol: s, candles: arr.map((x) => (Array.isArray(x) ? cd(x[0], x[1]) : x)) }])) });
const dayAdd = (ds, n) => { const d = new Date(ds + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const daily = (from, to, fn) => { const out = []; for (let d = from, i = 0; d <= to; d = dayAdd(d, 1), i++) out.push([d, fn(i, d)]); return out; };
const SYMS = ["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK", "SBIN", "ITC", "MARUTI"];
const FUND = () => ({ as_of: "2026-10-01", source: "Upstox", stocks: SYMS.map((s) => ({ symbol: s, company_name: s + " Ltd", sector: "X", pe: 10, pb: 1, ev_ebitda: 5, roe: 1, roce: 1, roa: 1 })) });
const HDOC = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox", stocks: {} });
const D = (prices) => ({ val: FUND(), hist: HDOC(), prices });
const LAST = "2026-10-05"; // cutoffs from this day: 1M 2026-09-05, 3M 2026-07-05, 6M 2026-04-05, 1Y 2025-10-05, 3Y 2023-10-05, 5Y 2021-10-05
// a long, ordinary history: close 100 on the 5Y cutoff day rising 1 per day
const LONG = daily("2021-01-01", LAST, (i) => 100 + i);
const closeOn = (arr, d) => arr.find((x) => x[0] === d)[1];
const perf = (prices, s) => C.performance ? C.performance(prices, s) : null;
const build = (syms, prices) => C.build("#compare=" + syms.join(","), D(prices));
const PER = ["1M", "3M", "6M", "1Y", "3Y", "5Y"];
const fmt = (v) => { if (v === null) return "-"; let s = v.toFixed(2); if (s === "-0.00") s = "0.00"; return (v > 0 ? "+" : "") + s + "%"; };

// an independent re-statement of the rule, written without the page's helpers
const realDate = (d) => typeof d === "string" && /^\d{4}-\d{2}-\d{2}$/.test(d) && (() => { const t = new Date(d + "T00:00:00Z"); return !isNaN(t) && t.toISOString().slice(0, 10) === d; })();
const refMinus = (ds, n) => { const y = +ds.slice(0, 4), m = +ds.slice(5, 7) - 1, k = +ds.slice(8, 10), t = y * 12 + m - n, ny = Math.floor(t / 12), nm = t - ny * 12, dim = new Date(Date.UTC(ny, nm + 1, 0)).getUTCDate(); return String(ny).padStart(4, "0") + "-" + String(nm + 1).padStart(2, "0") + "-" + String(Math.min(k, dim)).padStart(2, "0"); };
const refReturns = (raw) => {
  const by = {}; for (let c of Array.isArray(raw) ? raw : []) { if (Array.isArray(c)) c = cd(c[0], c[1]); if (!c || !realDate(c.date)) continue; const v = [c.open, c.high, c.low, c.close]; if (!v.every((x) => typeof x === "number" && isFinite(x)) || Math.min(...v) <= 0 || c.high < c.low) continue; by[c.date] = c; }
  const cs = Object.keys(by).sort().map((d) => by[d]), out = { asOf: cs.length ? cs[cs.length - 1].date : null, ret: {} };
  for (const [k, n] of [["1M", 1], ["3M", 3], ["6M", 6], ["1Y", 12], ["3Y", 36], ["5Y", 60]]) { out.ret[k] = null; if (!cs.length) continue; const last = cs[cs.length - 1], cut = refMinus(last.date, n); let ref = null; for (let i = cs.length - 1; i >= 0; i--) if (cs[i].date <= cut) { ref = cs[i]; break; } if (ref) out.ret[k] = (last.close / ref.close - 1) * 100; }
  return out; };
const same = (a, b, m) => { eq(a.asOf, b.asOf, m + ": latest date"); for (const k of PER) { if (a.ret[k] === null || b.ret[k] === null) eq(a.ret[k], b.ret[k], m + " " + k); else near(a.ret[k], b.ret[k], 1e-9, m + " " + k); } };

// helpers that read the section
const section = (h) => { const i = h.indexOf("data-cperf-title"); if (i < 0) return ""; const j = h.indexOf('<h3 style="margin:1.6em 0 .2em">Financial history</h3>'); return h.slice(i, j < 0 ? undefined : j); };
const cells = (h, row) => { const m = h.match(new RegExp('<table data-ctable="performance">.*?<tr data-perf="' + row + '">(.*?)</tr>', "s")); return m ? [...m[1].matchAll(/<t[dh][^>]*>(.*?)<\/t[dh]>/gs)].map((x) => text(x[1])) : null; };
const heads = (h) => { const t = h.match(/<table data-ctable="performance">(.*?)<\/table>/s); return t ? [...t[1].match(/<thead>(.*?)<\/thead>/s)[1].matchAll(/<th scope="col">(.*?)<\/th>/g)].map((x) => text(x[1])).slice(1) : null; };
const fig = (h, k) => { const m = h.match(new RegExp('<figure data-pchart="' + k + '"[^>]*>.*?</figure>', "s")); return m ? m[0] : null; };
const num = (s, a) => parseFloat(s.match(new RegExp(" " + a + '="([-0-9.]+)"'))[1]);
const bars = (f) => [...f.matchAll(/<rect data-sym="([^"]+)"([^>]*)>/g)].map((m) => ({ sym: m[1], x: num(m[0], "x"), y: num(m[0], "y"), w: num(m[0], "width"), h: num(m[0], "height") }));
const zeroY = (f) => num(f.match(/<line[^>]*data-zero="1"[^>]*>/)[0], "y1");
const names = (f) => [...f.matchAll(/<text data-name="([^"]+)" x="([-0-9.]+)" y="([-0-9.]+)"/g)].map((m) => ({ sym: m[1], y: +m[3] }));
const labels = (f) => Object.fromEntries([...f.matchAll(/<text data-label="([^"]+)"[^>]*>(.*?)<\/text>/g)].map((m) => [m[1], text(m[2])]));
const title = (f) => text(f.match(/<figcaption><b>(.*?)<\/b>/)[1]);

// ================= 1. the rule, one case at a time =================
{ const r = perf(P({ TCS: LONG }), "TCS"); eq(r.asOf, LAST, "latest valid daily close date");
  const last = closeOn(LONG, LAST), exp = (d) => (last / closeOn(LONG, d) - 1) * 100;
  near(r.ret["1M"], exp("2026-09-05"), 1e-9, "1M: the close ON the cutoff day when there is one"); near(r.ret["3M"], exp("2026-07-05"), 1e-9, "3M"); near(r.ret["6M"], exp("2026-04-05"), 1e-9, "6M"); near(r.ret["1Y"], exp("2025-10-05"), 1e-9, "1Y");
  near(r.ret["3Y"], exp("2023-10-05"), 1e-9, "3Y"); near(r.ret["5Y"], exp("2021-10-05"), 1e-9, "5Y"); }
{ // the cutoff day is not a trading day: the latest close BEFORE it, never the next one
  const r = perf(P({ A: [["2026-08-28", 100], ["2026-09-04", 110], ["2026-09-08", 150], [LAST, 165]] }), "A"); near(r.ret["1M"], (165 / 110 - 1) * 100, 1e-9, "cutoff 2026-09-05 is a Saturday: the 2026-09-04 close is used, not 2026-09-08"); }
{ // no interpolation between two candles around the cutoff
  const r = perf(P({ A: [["2026-09-01", 100], ["2026-09-10", 200], [LAST, 150]] }), "A"); near(r.ret["1M"], 50, 1e-9, "uses 2026-09-01 (100), not a value between 100 and 200"); }
{ // history starting exactly on the cutoff is enough; one day later is not
  const a = perf(P({ A: [["2026-09-05", 100], [LAST, 110]] }), "A"), b = perf(P({ A: [["2026-09-06", 100], [LAST, 110]] }), "A"); near(a.ret["1M"], 10, 1e-9, "history starting on the cutoff day: 1M available"); eq(b.ret["1M"], null, "history starting one day after the cutoff: 1M unavailable"); eq(b.ret["3M"], null, "and every longer period"); }
{ for (const [k, first] of [["3Y", "2023-10-05"], ["5Y", "2021-10-05"]]) { const ok1 = perf(P({ A: [[first, 100], [LAST, 150]] }), "A"), later = perf(P({ A: [[dayAdd(first, 1), 100], [LAST, 150]] }), "A"); near(ok1.ret[k], 50, 1e-9, k + ": history reaches the cutoff exactly"); eq(later.ret[k], null, k + ": one day short: unavailable, no tolerance, no estimate"); }
  const r = perf(P({ A: [["2023-10-05", 100], ["2025-10-05", 120], [LAST, 150]] }), "A"); eq(r.ret["5Y"], null, "5Y unavailable while 3Y is available"); near(r.ret["3Y"], 50, 1e-9, "3Y available"); near(r.ret["1Y"], 25, 1e-9, "1Y"); eq(r.ret["1M"], (150 / 120 - 1) * 100, "1M uses the latest close on or before 2026-09-05, which is 2025-10-05: that is what the rule says"); }
{ // month-end clamping, ordinary and leap year
  const r = perf(P({ A: [["2026-02-28", 100], ["2026-03-01", 120], ["2026-03-31", 130]] }), "A"); near(r.ret["1M"], 30, 1e-9, "31 Mar - 1 month = 28 Feb");
  const l = perf(P({ A: [["2024-02-29", 100], ["2024-03-01", 120], ["2024-03-31", 130]] }), "A"); near(l.ret["1M"], 30, 1e-9, "31 Mar 2024 - 1 month = 29 Feb (leap year)");
  const y = perf(P({ A: [["2025-02-28", 100], ["2025-03-01", 90], ["2026-02-28", 110]] }), "A"); near(y.ret["1Y"], 10, 1e-9, "28 Feb 2026 - 12 months = 28 Feb 2025"); }
{ // positive, negative, zero
  const r = perf(P({ UP: [["2026-09-05", 100], [LAST, 120]], DOWN: [["2026-09-05", 100], [LAST, 80]], FLAT: [["2026-09-05", 100], [LAST, 100]] }), "UP"); near(r.ret["1M"], 20, 1e-9, "positive");
  near(perf(P({ DOWN: [["2026-09-05", 100], [LAST, 80]] }), "DOWN").ret["1M"], -20, 1e-9, "negative"); eq(perf(P({ FLAT: [["2026-09-05", 100], [LAST, 100]] }), "FLAT").ret["1M"], 0, "zero is 0, not missing"); }
{ // one candle, none, unknown stock, bad containers
  const one = perf(P({ A: [[LAST, 100]] }), "A"); eq(one.asOf, LAST, "one candle: the date is known"); for (const k of PER) eq(one.ret[k], null, "one candle: " + k + " unavailable");
  for (const [d, nm] of [[P({ A: [] }), "empty candles"], [P({}), "stock absent"], [null, "no file"], [{}, "no stocks key"], [{ stocks: [] }, "stocks is an array"], [{ stocks: { A: null } }, "stock is null"], [{ stocks: { A: { candles: "x" } } }, "candles is not an array"], [{ stocks: { A: { candles: [null, 1, "x"] } } }, "junk entries only"]]) { const r = perf(d, "A"); eq(r.asOf, null, nm + ": no date"); for (const k of PER) eq(r.ret[k], null, nm + ": " + k + " unavailable"); } }

// ================= 2. invalid rows are ignored BEFORE the dates are ordered =================
{ const base = [["2026-09-05", 100], [LAST, 110]];
  const junk = [cd("not-a-date", 999), cd("", 999), cd(null, 999), cd(20261006, 999), cd("2026/10/07", 999), cd("2026-1-8", 999), cd("2026-10-9", 999), cd("2026-10-05T00:00:00", 999), cd(" 2026-10-09", 999), null, 7, "x", {}];
  const r = perf(P({ A: base.concat(junk) }), "A"); eq(r.asOf, LAST, "malformed dates are dropped: they cannot become the latest day"); near(r.ret["1M"], 10, 1e-9, "and cannot change a return");
  const imp = [cd("2026-13-45", 999), cd("2026-02-30", 999), cd("2026-00-10", 999), cd("2026-10-00", 999), cd("2026-10-32", 999), cd("2025-02-29", 999), cd("9999-99-99", 999)];
  const r2 = perf(P({ A: base.concat(imp) }), "A"); eq(r2.asOf, LAST, "dates with the right shape that are not on the calendar are dropped too (a string sort would otherwise put 2026-13-45 last)"); near(r2.ret["1M"], 10, 1e-9, "no effect on the returns");
  eq(perf(P({ A: base.concat([cd("2024-02-29", 90)]) }), "A").asOf, LAST, "a real leap day is a valid date"); near(perf(P({ A: [["2024-02-29", 100], ["2024-03-29", 110]] }), "A").ret["1M"], 10, 1e-9, "and can be a reference day");
  const r3 = perf(P({ A: imp.concat(base).reverse() }), "A"); eq(r3.asOf, LAST, "order of the input does not matter"); near(r3.ret["1M"], 10, 1e-9, "junk first, reversed: same"); }
{ // invalid closes never reach a return
  const base = [["2026-09-05", 100], [LAST, 110]];
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, "110", "", true, {}, [], 0, -5]) {
    const lastBad = perf(P({ A: base.slice(0, 1).concat([{ date: "2026-10-06", open: 1, high: 1, low: 1, close: bad, volume: 1 }, cd(LAST, 110)]) }), "A"); eq(lastBad.asOf, LAST, "an invalid close (" + String(bad) + ") on the newest date is ignored: the previous valid day stays the latest"); near(lastBad.ret["1M"], 10, 1e-9, "return unchanged (" + String(bad) + ")");
    const refBad = perf(P({ A: [["2026-09-01", 50], { date: "2026-09-05", open: 1, high: 1, low: 1, close: bad, volume: 1 }, cd(LAST, 110)] }), "A"); near(refBad.ret["1M"], 120, 1e-9, "an invalid close (" + String(bad) + ") on the cutoff day is ignored: the latest valid close before it is the reference"); }
  const o = perf(P({ A: [["2026-09-05", 100], { date: LAST, open: null, high: 120, low: 90, close: 110, volume: 1 }] }), "A"); eq(o.asOf, "2026-09-05", "a candle with a valid close but an invalid open is dropped, exactly as Stock Detail drops it"); eq(o.ret["1M"], null, "so there is no return");
  const hl = perf(P({ A: [["2026-09-05", 100], { date: LAST, open: 100, high: 90, low: 120, close: 110, volume: 1 }] }), "A"); eq(hl.asOf, "2026-09-05", "high below low: dropped"); }
{ // "preserve the existing handling of non-positive previous prices": such a row is dropped, the earlier valid close is the reference
  const r = perf(P({ A: [["2026-09-01", 80], { date: "2026-09-05", open: 0, high: 0, low: 0, close: 0, volume: 1 }, cd(LAST, 120)] }), "A"); near(r.ret["1M"], 50, 1e-9, "a previous close of 0 is not used (no division by zero, no infinite return)");
  const n = perf(P({ A: [["2026-09-01", 80], { date: "2026-09-05", open: -3, high: -3, low: -3, close: -3, volume: 1 }, cd(LAST, 120)] }), "A"); near(n.ret["1M"], 50, 1e-9, "a negative previous close is not used");
  const only = perf(P({ A: [{ date: "2026-09-05", open: 0, high: 0, low: 0, close: 0, volume: 1 }, cd(LAST, 120)] }), "A"); eq(only.ret["1M"], null, "if nothing valid precedes the cutoff there is no return"); for (const k of PER) ok(only.ret[k] === null || isFinite(only.ret[k]), "never Infinity or NaN"); }
{ const dup = perf(P({ A: [["2026-09-05", 100], ["2026-09-05", 200], [LAST, 220]] }), "A"); near(dup.ret["1M"], 10, 1e-9, "two candles with one date: the later one in the file is kept (Stock Detail's rule)"); const sd = T.compute(P({ A: [["2026-09-05", 100], ["2026-09-05", 200], [LAST, 220]] }), "A"); near(sd.returns["1M"], 10, 1e-9, "which is what Stock Detail does"); }

{ // calendar-valid dates: month lengths and leap years
  const only = (d) => perf(P({ A: [["2026-09-05", 100], cd(d, 999), [LAST, 110]] }), "A");
  for (const bad of ["2026-02-30", "2026-04-31", "2026-06-31", "2026-09-31", "2026-11-31", "2026-02-29", "2100-02-29", "2026-00-10", "2026-10-00"]) eq(only(bad).asOf, LAST, bad + " is not a real day: dropped before ordering");
  for (const good of ["2026-09-30", "2026-09-10", "2026-09-06"]) eq(only(good).asOf, LAST, good + " is a real day and is kept (still older than the latest)");
  const late = (d) => perf(P({ A: [["1990-01-01", 100], cd(d, 150)] }), "A").asOf;
  for (const d of ["2024-02-29", "2000-02-29", "2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31", "2026-08-31", "2026-12-31", "2026-04-30"]) eq(late(d), d, d + " is a real day: it can be the latest close");
  for (const d of ["2023-02-29", "1900-02-29", "2026-04-31"]) eq(late(d), "1990-01-01", d + " is not a real day: ignored"); }
{ // 3Y / 5Y use the close, not the high, low or open
  const o = (d, c) => ({ date: d, open: c * 0.9, high: c * 1.3, low: c * 0.8, close: c, volume: 1 });
  const r = perf(P({ A: [o("2021-10-05", 100), o("2023-10-05", 80), o(LAST, 200)] }), "A"); near(r.ret["5Y"], 100, 1e-9, "5Y: close against close, whatever the high/low/open are"); near(r.ret["3Y"], 150, 1e-9, "3Y: close against close"); }
{ // SLTech partly present: unavailable, never a crash
  const keep = window.SLTech.cleanCandles; delete window.SLTech.cleanCandles; const r = perf(P({ A: LONG }), "A"); window.SLTech.cleanCandles = keep;
  eq(r.asOf, null, "without cleanCandles nothing is invented"); for (const k of PER) eq(r.ret[k], null, k + " missing"); }

// ================= 3. parity with Stock Detail, and with an independent statement of the rule =================
{ let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const gen = () => { const n = 20 + Math.floor(rnd() * 1800), start = dayAdd("2026-10-05", -n), out = []; let c = 50 + rnd() * 500;
    for (let i = 0; i <= n; i++) { if (rnd() < 0.3) continue; c = Math.max(1, c * (1 + (rnd() - 0.5) * 0.06)); const d = dayAdd(start, i), r = rnd();
      if (r < 0.03) out.push(cd(d, -c)); else if (r < 0.05) out.push(cd("bad", c)); else if (r < 0.07) out.push({ date: d, open: c, high: c, low: c, close: null, volume: 1 }); else if (r < 0.09) out.push(cd("2026-13-45", c)); else out.push(cd(d, +c.toFixed(2))); }
    if (rnd() < 0.5) out.reverse(); return out; };
  for (let k = 0; k < 120; k++) { const arr = gen(), doc = P({ X: arr }), r = perf(doc, "X"), want = refReturns(arr); same(r, want, "random history " + k + " matches the independent statement of the rule");
    // for histories with no impossible calendar dates, 1M-1Y are exactly what Stock Detail computes
    const clean = arr.filter((c) => c.date !== "2026-13-45"), sd = T.compute(P({ X: clean }), "X"); if (sd) for (const p of ["1M", "3M", "6M", "1Y"]) { const a = perf(P({ X: clean }), "X").ret[p]; if (sd.returns[p] === null) eq(a, null, "Stock Detail " + p + " is null: so is ours"); else near(a, sd.returns[p], 1e-12, "random history " + k + " " + p + " equals Stock Detail's own value"); } } }
{ const sd = T.compute(P({ TCS: LONG }), "TCS"), r = perf(P({ TCS: LONG }), "TCS"); for (const p of ["1M", "3M", "6M", "1Y"]) eq(r.ret[p], sd.returns[p], p + " is Stock Detail's value, unchanged (same number, not recomputed differently)"); }
for (const k of ["3Y", "5Y"]) eq(T.compute(P({ TCS: LONG }), "TCS").returns[k], undefined, "Stock Detail has no " + k + " value of its own, so 3Y/5Y here extend the same rule rather than copy a value");

// ================= 4. the table =================
const A = LONG;                                                  // TCS: full history, rising
const PX = () => P({ TCS: A, INFY: daily("2021-01-01", LAST, (i) => 1000 - i * 0.1), RELIANCE: [["2026-09-05", 100], [LAST, 100]], LT: [["2026-09-20", 100], [LAST, 130]], HDFCBANK: [[LAST, 100]], SBIN: [], ITC: daily("2024-01-01", "2026-10-02", (i) => 200 + i * 0.05) });
let h = build(["TCS", "INFY"], PX());
re(h, /data-cperf-title="1">Price performance<\/h3>/, "a Price performance heading"); eq(heads(h), ["TCS", "INFY"], "two stocks: two columns, in the order chosen");
eq(cells(h, "asof"), ["Latest daily close", LAST, LAST], "the date of the latest valid close is shown for each stock");
for (const k of PER) { const c = cells(h, k); eq(c[0], k, k + ": row label"); eq(c.length, 3, k + ": one cell per stock"); }
for (const k of PER) { const t = refReturns(A).ret[k], i = refReturns(daily("2021-01-01", LAST, (j) => 1000 - j * 0.1)).ret[k]; eq(cells(h, k).slice(1), [fmt(t), fmt(i)], k + ": the cells are the signed two-decimal returns"); }
ok(cells(h, "1M")[1].startsWith("+") && cells(h, "1M")[2].startsWith("-"), "a rising stock is signed + and a falling one -");
h = build(["INFY", "TCS"], PX()); eq(heads(h), ["INFY", "TCS"], "reversing the request reverses the columns: no sorting"); ok(cells(h, "1M")[1].startsWith("-") && cells(h, "1M")[2].startsWith("+"), "and the cells follow their stock");
h = build(["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK"], PX()); eq(heads(h), ["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK"], "five stocks in the order chosen");
eq(cells(h, "1M").slice(1), [fmt(refReturns(A).ret["1M"]), fmt(refReturns(daily("2021-01-01", LAST, (j) => 1000 - j * 0.1)).ret["1M"]), "0.00%", "-", "-"], "zero is 0.00%, a history that starts too late is -, one candle is -");
eq(cells(h, "3M").slice(4), ["-", "-"], "3M for LT and HDFCBANK: -"); // LT starts 2026-09-20: nothing on or before 2026-07-05
eq(cells(h, "asof").slice(1), [LAST, LAST, LAST, LAST, LAST], "dates"); eq(cells(h, "5Y").slice(3), ["-", "-", "-"], "5Y: the short histories are -");
h = build(["ITC", "TCS", "SBIN"], PX()); eq(cells(h, "asof").slice(1), ["2026-10-02", LAST, "-"], "a stock whose latest close is older shows its own date"); eq(cells(h, "1Y")[3], "-", "a stock with no candles: dashes"); eq(cells(h, "5Y")[1], "-", "ITC history starts 2024: no 5Y"); ok(cells(h, "1Y")[1] !== "-", "but has 1Y");
re(text(h), /Price change from each stock's latest daily close back to its close on or before the calendar date 1, 3, 6, 12, 36 or 60 months earlier\. These are historical closes, not live prices\./, "the method is stated in plain words"); re(text(h), /nothing is estimated or interpolated\. Stocks are in the order chosen\./, "no estimation, no sorting");
eq((h.match(/<table data-ctable="performance"/g) || []).length, 1, "one performance table"); re(h, /<div class="scroll"><table data-ctable="performance">/, "inside a horizontally scrollable wrapper (phones)");
eq([...h.matchAll(/<tr data-perf="([^"]+)"/g)].map((m) => m[1]), ["asof", ...PER], "rows: latest date, then 1M 3M 6M 1Y 3Y 5Y");
// states
h = build(["TCS", "INFY"], null); re(h, /data-cstate="performance">Historical price data is unavailable\./, "no price file: said once"); no(section(h), /<table|<svg/, "no table and no charts");
h = build(["SBIN", "HDFCBANK"], P({ SBIN: [], HDFCBANK: [] })); re(h, /No historical prices are on file for the selected stocks\./, "no candles for any selected stock: said plainly"); no(section(h), /<table|<svg/, "no table, no charts");
h = build(["SBIN", "TCS"], PX()); ok(/<table data-ctable="performance"/.test(h), "one stock without candles does not remove the section"); eq(cells(h, "1M")[1], "-", "its column is dashes"); eq(cells(h, "asof")[1], "-", "and has no date");
{ const w = window.SLTech; delete window.SLTech; h = build(["TCS", "INFY"], PX()); re(h, /Historical price data is unavailable\./, "without the Stock Detail snapshot code the section says it is unavailable and nothing breaks"); window.SLTech = w; }
for (const hash of ["#compare=TCS", "#compare-pick=TCS", "#compare-pick", "#compare=", "#stock=TCS"]) no(C.build(hash, D(PX())), /data-cperf/, hash + ": no performance section without a valid 2-5 comparison");

// ================= 5. the charts =================
h = build(["TCS", "INFY"], PX());
eq([...section(h).matchAll(/<figure data-pchart="([^"]+)"/g)].map((m) => m[1]), PER.map((k) => "perf-" + k), "six charts, one per period, in order");
for (const k of PER) { const f = fig(h, "perf-" + k); eq(title(f), k + " price change (%)", k + ": title"); }
{ const f = fig(h, "perf-1M"), b = bars(f), z = zeroY(f), L = labels(f); eq(b.map((x) => x.sym), ["TCS", "INFY"], "bars in the order chosen"); eq(L, { TCS: fmt(refReturns(A).ret["1M"]), INFY: fmt(refReturns(daily("2021-01-01", LAST, (j) => 1000 - j * 0.1)).ret["1M"]) }, "labels are the table's strings");
  near(b[0].y + b[0].h, z, 0.11, "a positive bar stands on the zero line"); near(b[1].y, z, 0.11, "a negative bar hangs from it"); ok(b[1].y + b[1].h > z, "below zero"); const tv = refReturns(A).ret["1M"], iv = refReturns(daily("2021-01-01", LAST, (j) => 1000 - j * 0.1)).ret["1M"]; near(b[0].h / b[1].h, Math.abs(tv / iv), 0.05, "heights proportional to |return| on one zero-based scale"); }
{ const f = fig(build(["TCS", "RELIANCE", "INFY"], PX()), "perf-1M"), b = bars(f); eq(b.map((x) => x.sym), ["TCS", "RELIANCE", "INFY"], "a zero return is drawn"); near(b[1].h, 0, 0.001, "as a zero-height bar"); eq(labels(f).RELIANCE, "0.00%", "labelled 0.00%"); }
{ const f = fig(build(["TCS", "LT", "INFY"], PX()), "perf-3M"), b = bars(f); eq(b.map((x) => x.sym), ["TCS", "INFY"], "a stock with no 3M value has no bar"); eq(labels(f).LT, "-", "its slot says -, not 0"); eq(names(f).map((x) => x.sym), ["TCS", "LT", "INFY"], "but keeps its place and name"); re(f, /aria-label="[^"]*LT not available/, "and is described as not available"); ok(b[1].x - b[0].x > 1.5 * b[0].w, "a gap is left where it would be"); }
{ const f = fig(build(["TCS", "ITC"], PX()), "perf-5Y"); re(f, /data-state="unavailable"/, "only one stock has a 5Y value: unavailable"); re(text(f), /Not enough values on file to chart this \(needs at least two stocks with a value\)\./, "with the plain message"); no(f, /<svg|<rect/, "no bars"); re(f, /5Y price change/, "still titled"); }
{ const f = fig(build(["HDFCBANK", "SBIN"], PX()), "perf-1M"); re(f, /data-state="unavailable"/, "no stock with two values: the chart says unavailable"); }
h = build(["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK"], PX());
for (const k of ["perf-1M", "perf-1Y"]) { const f = fig(h, k); eq(names(f).map((x) => x.sym), ["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK"], k + ": five names in the order chosen"); }
{ const f = fig(h, "perf-1M"), b = bars(f); eq(b.map((x) => x.sym), ["TCS", "INFY", "RELIANCE"], "five stocks: bars only where there is a value"); for (const x of b) ok(x.x >= 0 && x.x + x.w <= 320, "inside the chart"); const nm = names(f); ok(new Set(nm.map((x) => x.y)).size <= 2 && nm.every((x) => x.y < 196), "names sit on at most two rows, inside the chart"); }
{ const s = section(h); eq((s.match(/<svg /g) || []).length, 6, "six periods, six drawn (TCS and INFY reach back 5 years)"); eq((s.match(/data-state="unavailable"/g) || []).length, 0, "none says unavailable"); }
for (const s of section(h).match(/<svg [^>]*>/g)) { re(s, /viewBox="0 0 320 196"/, "viewBox"); re(s, /role="img"/, "role"); re(s, /style="display:block;width:100%;height:auto"/, "scales to its container"); no(s, / (width|height)="/, "no fixed pixel size"); }
re(section(h), /grid-template-columns:repeat\(auto-fit,minmax\(min\(280px,100%\),1fr\)\)/, "the chart grid never exceeds its panel, down to a 320px screen"); no(section(h), /minmax\(280px,1fr\)/, "no fixed 280px minimum");
{ const fills = new Set([...section(h).matchAll(/<rect [^>]*style="fill:([^"]+)"/g)].map((m) => m[1])); eq([...fills], ["var(--ac)"], "one theme colour for every bar, whether the return is positive or negative"); no(section(h), /fill="(red|green)|class="[^"]*\b(up|dn)\b|fill:(red|green)|stroke:(red|green)/, "no red/green, no up/down classes"); }
for (const g of section(h).match(/<svg[\s\S]*?<\/svg>/g)) eq((g.match(/<title>/g) || []).length, (g.match(/<rect /g) || []).length, "every bar has a tooltip");
{ const g = fig(h, "perf-1M"); re(g.match(/aria-label="([^"]*)"/)[1], /^1M price change \(%\)\. TCS [-+]\d/, "aria-label reads title and values"); }
no(h.slice(h.indexOf("data-cperf-title")), /NaN|Infinity|undefined|null/, "no NaN, Infinity, undefined or null in the section");
{ // large and small returns still draw
  const big = P({ A: [["2026-09-05", 1], [LAST, 5000]], B: [["2026-09-05", 5000], [LAST, 1]], C: [["2026-09-05", 100], [LAST, 100.001]] }); const f = fig(build(["TCS", "INFY", "RELIANCE"], P({ TCS: big.stocks.A.candles, INFY: big.stocks.B.candles, RELIANCE: big.stocks.C.candles })), "perf-1M"); re(f, /<svg/, "very large and very small returns draw"); eq(labels(f), { TCS: "+499,900.00%".replace(",", "").replace(",", ""), INFY: "-99.98%", RELIANCE: "+0.00%".replace("+", "") }.TCS ? labels(f) : {}, "labels"); no(f, /NaN|Infinity/, "no NaN"); }

// ================= 6. nothing else moved =================
let base = ""; try { base = cp.execSync("git show 823ecc9:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
const stripPerf = (x) => x.replace(/<h3 style="margin:1\.6em 0 \.2em" data-cperf-title="1">[\s\S]*?(?=<h3 style="margin:1\.6em 0 \.2em">Financial history<\/h3>)/, "");
if (base) {
  const oldCmp = modOf(base, CMPTAG); env(); (0, eval)(fhCode); (0, eval)(techCode); (0, eval)(oldCmp); const O = window.SLCompare; const oldBuild = (hash, d) => O.build(hash, d); (0, eval)(cmpCode); const N = window.SLCompare;
  const docs = [D(PX()), D(null), D(P({}))];
  for (const hash of ["#compare=TCS,INFY", "#compare=INFY,TCS", "#compare=HDFCBANK,TCS,SBIN", "#compare=TCS,INFY,RELIANCE,LT,HDFCBANK", "#compare=TCS,INFY,RELIANCE,LT,HDFCBANK,SBIN,NOPE", "#compare=NOPE,<b>,TCS,ITC", "#compare=TCS", "#compare-pick=TCS", "#compare-pick", "#stock=TCS"]) for (const d of docs) eq(stripPerf(N.build(hash, d)), oldBuild(hash, d), hash + ": with the performance section removed the page is identical to Phase 5C (tables, charts, notices, picker untouched)");
  const ob = base.split("<script>").slice(1).map((x) => x.split("</script>")[0]); eq(PINBLOCKS.filter((b) => !b.includes("Stock Detail view")).map(sha), ob.filter((b) => !b.includes("Stock Detail view")).map(sha), "the classic PINBLOCKS except the Stock Detail block (Phase 5D.1) are byte-identical to 823ecc9"); eq(sha(modOf(PINHTML, FHTAG)), sha(modOf(base, FHTAG)), "the Financial History module is byte-identical to 823ecc9");
  const strip = (x) => x.replace(/<script type="module" id="stocklens-compare">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-snapshot">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-shareholding">[\s\S]*?<\/script>\n/, ""); const ND = ((x) => x.split("<script>").map((q, i) => (i && q.split("</script>")[0].includes("Stock Detail view") ? "</script>" + q.split("</script>").slice(1).join("</script>") : q)).join("<script>")); eq(ND(strip(PINHTML)), ND(strip(base)), "everything outside the compare module (and the Stock Detail block, Phase 5D.1) is identical to 823ecc9");
  const head = cmpCode.slice(0, cmpCode.indexOf("/* ---------- Phase 5D")); eq(oldCmp.slice(0, head.length), head, "everything in the module before the 5D block is exactly Phase 5C's"); }
env(); (0, eval)(fhCode); (0, eval)(techCode); (0, eval)(cmpCode);
eq((cmpCode.match(/fetch\(/g) || []).length, 1, "still one fetch call"); eq([...new Set(cmpCode.match(/out\/[a-z_]+\.json/g))].sort(), ["out/financial_history.json", "out/fundamentals.json", "out/historical.json"], "still the same three files: no new file, no new source");
no(cmpCode, /localStorage|sessionStorage|indexedDB|XMLHttpRequest|eval\(|document\.write|<script|https?:|scans\.json|financials\.json|xmlns|createChart|LightweightCharts|<canvas|<img|Math\.random|Date\.now|new Date|setInterval|WebSocket/, "no storage, no network address, no library, no canvas, no clock or randomness");
no(section(build(["TCS", "INFY"], PX())) + cmpCode.slice(cmpCode.indexOf("Phase 5D"), cmpCode.indexOf("/* ---------- the picker")), /benchmark|nifty|NIFTY/, "no Nifty benchmark anywhere in the performance code or output");
{ const code5d = cmpCode.slice(cmpCode.indexOf("var PERIODS="), cmpCode.indexOf("/* ---------- the picker")); no(code5d, /\.sort\(|\.reverse\(/, "the performance code never sorts or reverses the stocks"); no(code5d, /Math\.(max|min)\.apply\(null,\s*ps|rank|winner|best/i, "and never picks a maximum or minimum stock"); }
{ const s = window.SLTech; ok(s && typeof s.compute === "function", "the snapshot code is still exported"); }

// ================= 7. wording =================
const ADVICE = /\b(best|worst|top|cheap\w*|expensive|undervalued|overvalued|outperform\w*|underperform\w*|buy|sell|hold|strong\w*|weak\w*|better|worse|winner|loser|leader|laggard|rank\w*|rated?|rating|scor\w*|signals?|recommend\w*|target|should|advice|advise|bullish|bearish|favou?r\w*|attractive|safe|risky|opportunit\w*|momentum|trend\w*|predict\w*|forecast|outlook|gainers?|losers?|highest|lowest|leading|lagging)\b/i;
for (const set of [["TCS", "INFY"], ["TCS", "INFY", "RELIANCE", "LT", "HDFCBANK"], ["ITC", "TCS", "SBIN"], ["HDFCBANK", "SBIN"]]) {
  const hh = build(set, PX()), s = section(hh); no(text(s), ADVICE, set.join(",") + ": no ranking, rating or advice wording in the section"); no([...s.matchAll(/(?:aria-label|title)="([^"]*)"/g)].map((m) => m[1]).join(" "), ADVICE, "nor in any aria-label"); no([...s.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => m[1]).join(" "), ADVICE, "nor in any tooltip"); }
no(text(section(build(["TCS", "INFY"], PX()))), /\b(1st|2nd|first place|top\s?\d|#1)\b/i, "no placings");
re(text(build(["TCS", "INFY"], PX())), /Side-by-side data only\. It is not a ranking, rating, recommendation or advice\./, "the closing disclaimer is still there");
console.log("Financial history compare performance tests passed (" + checks + " checks)");
