// Run: node tests/test_financial_history_charts.js   (no network, no browser; stubs the DOM, MutationObserver and fetch)
// Tests the Phase 5A Financial History charts (inline SVG): one chart per measure, same values/strings as the table, zero baseline, negatives below
// the line, missing years left empty (never bridged or zero), unavailable states, bank "Total income", official-year hatching, the ITC divider,
// no growth figures or advice wording, no library/network use, responsive markup, and that the table / change / CAGR output is unchanged from 3c07ada.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const MARK = "Phase 4 Step 4E - Financial History", MOD = '<script type="module">';
const modOf = (src) => (src.split(MOD)[1] || "").split("</script>")[0];
const fhCode = modOf(html);
const detailCode = blocks.find((b) => b.includes("Stock Detail view")), chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart")),
  techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot")), researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research")),
  checkCode = blocks.find((b) => b.includes("Phase 4 Step 2 - Investment Checklist"));
assert.ok(fhCode.includes(MARK) && detailCode && chartCode && techCode && researchCode && checkCode, "script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
const load = (code) => { global.window = {}; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
  return new Function(code.replace('"use strict";', "").replace("(function(){", "var window={location:{hash:\"\"}};(function(){").replace(/\}\)\(\);\s*$/, "})();return window.SLFinHistory;"))(); };
const F = load(fhCode);

// ---------- fixtures (tests only) ----------
const V = (o) => Object.assign({ revenue: null, total_revenue: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, eps_diluted: null, operating_cash_flow: null, investing_cash_flow: null, financing_cash_flow: null, capex: null, free_cash_flow: null }, o);
const R = (fy, values, { official = false, mismatch = false } = {}) => ({ fy, period: "Mar " + fy, basis: "consolidated", values: V(values),
  verification: { status: "verified", reconciliation: official ? "not_checked" : mismatch ? "mismatch" : "matched", warnings: [], checks: {} }, source: { provider: official ? "Official annual report" : "Upstox" }, revisions: [] });
const full = (fy, v, o = {}, extra = {}) => R(fy, { revenue: v, total_revenue: v * 1.02, profit_before_tax: v / 2, profit_after_tax: v / 4, eps_basic: v / 10, operating_cash_flow: v / 3, ...extra }, o);
const run = (fy0, n, start, f = 1.1) => Array.from({ length: n }, (_, i) => full(fy0 + i, +(start * Math.pow(f, i)).toFixed(2)));
const DOC = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox (API) and official company annual reports", stocks: {
  TCS: { symbol: "TCS", statement_layout: "standard", years: [full(2022, 100, { official: true }), ...run(2023, 4, 110)] },
  INFY: { symbol: "INFY", statement_layout: "standard", years: run(2023, 4, 200) },
  ITC: { symbol: "ITC", statement_layout: "standard", years: [full(2022, 50, { official: true }), ...run(2023, 4, 100).map((y) => { y.verification.reconciliation = "mismatch"; return y; })] },
  HDFCBANK: { symbol: "HDFCBANK", statement_layout: "financial", years: run(2023, 4, 204666.1).map((y, i) => { y.values.revenue = null; y.values.operating_cash_flow = [20813.7, -19069.3, 127242, -113506][i]; return y; }) },
  GAP: { symbol: "GAP", statement_layout: "standard", years: [full(2023, 10), full(2024, 11), full(2026, 14)] },
  HOLE: { symbol: "HOLE", statement_layout: "standard", years: [full(2023, 10), full(2024, 11), full(2025, 12, {}, { revenue: null }), full(2026, 14)] },
  SOLO: { symbol: "SOLO", statement_layout: "standard", years: [full(2026, 100)] },
  TWO: { symbol: "TWO", statement_layout: "standard", years: [full(2023, 100), full(2026, 130)] },
  NULLREV: { symbol: "NULLREV", statement_layout: "standard", years: run(2023, 4, 100).map((y) => { y.values.revenue = null; return y; }) },
  NEG: { symbol: "NEG", statement_layout: "standard", years: [full(2023, 100, {}, { operating_cash_flow: -86013.7 }), full(2024, 110, {}, { operating_cash_flow: 38097.4 }), full(2025, 120, {}, { operating_cash_flow: -5 }), full(2026, 130, {}, { operating_cash_flow: 0 })] },
  ZERO: { symbol: "ZERO", statement_layout: "standard", years: [full(2025, 0, {}, { operating_cash_flow: 0 }), full(2026, 0, {}, { operating_cash_flow: 0 })] },
  SAME: { symbol: "SAME", statement_layout: "standard", years: [full(2025, 7), full(2026, 7)] },
  LONG: { symbol: "LONG", statement_layout: "standard", years: run(2015, 12, 100) },
  OTHER: { symbol: "OTHER", statement_layout: "standard", years: [full(2022, 50, { official: true }), ...run(2023, 4, 100)] },
  BIGBANK: { symbol: "BIGBANK", statement_layout: "financial", years: run(2023, 4, 495462.81).map((y) => { y.values.revenue = null; return y; }) } } });
const page = (s, doc = DOC()) => F.build(s, doc);
const figure = (h, key) => { const m = h.match(new RegExp('<figure data-chart="' + key + '"[\\s\\S]*?</figure>')); return m ? m[0] : null; };
const keysOf = (h) => [...h.matchAll(/<figure data-chart="([a-z_]+)"/g)].map((m) => m[1]);
const rects = (f) => [...f.matchAll(/<rect data-fy="(\d+)" x="([\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)].map((m) => ({ fy: +m[1], x: +m[2], y: +m[3], w: +m[4], h: +m[5] }));
const zeroY = (f) => +f.match(/y1="(-?[\d.]+)" y2="-?[\d.]+" data-zero="1"/)[1];
const labels = (f) => Object.fromEntries([...f.matchAll(/<text data-label="(\d+)"[^>]*>([^<]*)<\/text>/g)].map((m) => [m[1], m[2].replace(/&amp;/g, "&")]));
const tableCells = (h, key) => { const m = h.match(new RegExp('<tr data-row="' + key + '">(.*?)</tr>', "s")); return [...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map((x) => text(x[1])).slice(1); };
const tableHeads = (h) => [...h.match(/<thead>(.*?)<\/thead>/s)[1].matchAll(/<th>(.*?)<\/th>/g)].map((x) => text(x[1])).filter(Boolean);

// ================= 1. five charts, in order, titled with their unit =================
let h = page("TCS");
eq(keysOf(h), ["revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "non-bank: five charts in the table's order");
eq([...h.matchAll(/<figcaption><b>([^<]*)<\/b>/g)].map((m) => m[1]), ["Revenue (₹ crore)", "Profit before tax (₹ crore)", "Profit after tax (₹ crore)", "EPS, basic (₹ per share)", "Operating cash flow (₹ crore)"], "titles carry the unit");
re(h, /<h5[^>]*>Charts<\/h5>/, "section heading"); re(text(h), /The same values as the table, one chart per measure\. Bars start at zero; a missing year is left empty\./, "plain-words key");
ok(h.indexOf(">Charts<") > h.indexOf('data-cagr="profit_after_tax"') && h.indexOf(">Charts<") < h.indexOf("Official annual report: the figures"), "charts sit after the CAGR lines and before the notes, inside the same panel");
eq((h.match(/<figure /g) || []).length, 5, "exactly five figures"); eq(rects(figure(h, "revenue")).map((r) => r.fy), [2022, 2023, 2024, 2025, 2026], "TCS: five columns FY2022-FY2026 from the real records");
for (const k of keysOf(h)) eq(rects(figure(h, k)).length, 5, k + ": five bars");

// ================= 2. four-year stock with no FY2022 =================
h = page("INFY"); eq(rects(figure(h, "revenue")).map((r) => r.fy), [2023, 2024, 2025, 2026], "INFY: four bars, no FY2022 slot"); no(figure(h, "revenue"), /FY22|FY2022/, "no FY2022 label or bar invented");
no(h, /Hatched/, "no official-year key when no year is official"); no(h, /data-official/, "no hatched bar"); no(h, /<pattern/, "no pattern defined");

// ================= 3. bank: Total income, never Revenue =================
h = page("HDFCBANK");
eq(keysOf(h), ["total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "bank: Total income replaces Revenue"); re(figure(h, "total_revenue"), /<b>Total income \(₹ crore\)<\/b>/, "bank title");
no(text(h).replace(/Total income/g, ""), /\bRevenue\b/, "the word Revenue never appears for a bank (table or charts)"); no(text(figure(h, "total_revenue")) + (figure(h, "total_revenue").match(/aria-label="[^"]*"|<title>[^<]*<\/title>/g) || []).join(" "), /Revenue/i, "no 'Revenue' in the bank chart's visible text, text alternative or tooltips");
eq(rects(figure(h, "total_revenue")).length, 4, "bank Total income: four bars");
h = page("TCS"); no(h, /Total income/, "non-bank never says Total income");
{ const d = DOC(); d.stocks.TCS.years.forEach((y) => { y.values.revenue = null; }); const hh = page("TCS", d); re(figure(hh, "revenue"), /data-state="unavailable"/, "non-bank with no Revenue: unavailable (total_revenue is never substituted)"); eq(rects(figure(hh, "revenue")).length, 0, "no bars drawn from total_revenue"); }

// ================= 4. the chart uses the table's values and strings =================
for (const sym of ["TCS", "ITC", "HDFCBANK", "NEG", "BIGBANK"]) {
  const hh = page(sym), cols = tableHeads(hh).map((x) => +x.slice(2));
  for (const key of keysOf(hh)) { const f = figure(hh, key), cells = tableCells(hh, key), lab = labels(f); cols.forEach((fy, i) => eq(lab[fy], cells[i], sym + " " + key + " FY" + fy + ": chart label equals the table cell")); }
}
{ const f = figure(page("TCS"), "eps_basic"); eq(Object.values(labels(f)), ["10.00", "11.00", "12.10", "13.31", "14.64"], "EPS labels use the table's two-decimal strings"); }

// ================= 5. zero baseline, bar geometry, negatives below the line =================
h = page("TCS"); { const f = figure(h, "revenue"), z = zeroY(f), rs = rects(f);
  for (const r of rs) ok(Math.abs(r.y + r.h - z) < 0.11, "positive bar FY" + r.fy + " sits on the zero line"); ok(rs.every((r) => r.h > 0), "positive values have height");
  ok(Math.abs(rs[4].h / rs[0].h - 146.41 / 100) < 0.02, "bar height is proportional to the value (100 → 146.41)"); }
h = page("NEG"); { const f = figure(h, "operating_cash_flow"), z = zeroY(f), rs = rects(f), by = Object.fromEntries(rs.map((r) => [r.fy, r]));
  ok(Math.abs(by[2023].y - z) < 0.11 && by[2023].h > 0, "negative bar hangs below the zero line (starts at it)"); ok(Math.abs(by[2024].y + by[2024].h - z) < 0.11, "positive bar stands on it");
  ok(Math.abs(by[2023].h / by[2024].h - 86013.7 / 38097.4) < 0.02, "heights stay proportional across the sign"); ok(by[2026].h === 0, "a real zero is a zero-height bar"); eq(labels(f)[2026], "0", "labelled 0 (not '-')");
  ok(by[2025].y >= z - 0.11, "small negative also below the line");
  const ax = F.niceAxis(-86013.7, 38097.4); ok(ax.min <= -86013.7 && ax.max >= 38097.4 && ax.ticks.includes(0), "axis covers both signs and includes 0"); }
{ for (const [lo, hi] of [[5, 9], [100, 200], [-3, -1], [0, 0], [7, 7], [-86013.7, 38097.4], [0.01, 0.02], [123456, 654321]]) { const a = F.niceAxis(lo, hi);
    ok(a.min <= Math.min(0, lo) && a.max >= Math.max(0, hi) && a.ticks.includes(0) && a.ticks.every((t, i) => !i || t > a.ticks[i - 1]) && a.ticks.length >= 2 && a.ticks.length <= 8, "axis for [" + lo + ", " + hi + "] starts at zero side, covers the data, ticks ascend"); } }
{ const f = figure(page("NEG"), "operating_cash_flow"), z = zeroY(f), ly = Object.fromEntries([...f.matchAll(/<text data-label="(\d+)" x="[\d.]+" y="(-?[\d.]+)"/g)].map((m) => [m[1], +m[2]])), by = Object.fromEntries(rects(f).map((r) => [r.fy, r]));
  ok(ly[2023] > by[2023].y + by[2023].h, "a negative bar's label is printed below the bar"); ok(ly[2024] < by[2024].y, "a positive bar's label is printed above the bar"); }
{ const rs = rects(figure(page("TCS"), "revenue")), step = rs[1].x - rs[0].x; ok(rs.every((r) => Math.abs(r.w / step - 0.6) < 0.03), "bars are 60% of their slot, evenly spaced"); ok(rs.every((r, i) => !i || Math.abs(r.x - rs[i - 1].x - step) < 0.2), "even spacing"); ok(rs[4].x + rs[4].w <= 320, "bars stay inside the canvas"); }
// all-positive values never get a truncated (non-zero) baseline
ok(F.niceAxis(100, 200).min === 0, "an all-positive series still starts its axis at 0"); eq(F.niceAxis(0, 0), { min: 0, max: 1, ticks: [0, 1] }, "all zeros: safe fallback axis");
h = page("ZERO"); { const f = figure(h, "revenue"); eq(rects(f).map((r) => r.h), [0, 0], "all-zero series: zero-height bars"); eq(Object.values(labels(f)), ["0", "0"], "labelled 0"); }
h = page("SAME"); eq(rects(figure(h, "revenue")).map((r) => r.h).every((x) => x > 0 && x === rects(figure(h, "revenue"))[0].h), true, "equal values give equal bars");
eq(F.niceAxis(0, 0).ticks.length, 2, "ticks");

// ================= 6. missing years and values =================
h = page("GAP"); { const f = figure(h, "revenue"), rs = rects(f);
  eq(rs.map((r) => r.fy), [2023, 2024, 2026], "GAP: bars only for years with data"); eq([...f.matchAll(/>FY(\d\d)<\/text>/g)].map((m) => m[1]), ["23", "24", "25", "26"], "GAP: FY2025 keeps an (empty) slot on the axis");
  const step = rs[1].x - rs[0].x; ok(Math.abs(rs[2].x - rs[1].x - 2 * step) < 0.2, "the gap year takes real space: FY2026 is two slots after FY2024 (never bridged)"); eq(labels(f)[2025], "-", "the empty slot is labelled '-'");
  no(f, /data-fy="2025"/, "no bar for FY2025"); }
h = page("HOLE"); { const f = figure(h, "revenue"); eq(rects(f).map((r) => r.fy), [2023, 2024, 2026], "HOLE: a null value draws no bar (never zero)"); eq(labels(f)[2025], "-", "null labelled '-'"); eq(rects(figure(h, "profit_before_tax")).length, 4, "other metrics of the same stock are unaffected"); }
// unavailable states
for (const [sym, key] of [["SOLO", "revenue"], ["NULLREV", "revenue"]]) { const f = figure(page(sym), key); re(f, /data-state="unavailable"/, sym + ": unavailable state"); re(text(f), /Not enough years on file to chart this \(needs at least two years with a value\)\./, sym + ": clear message"); no(f, /<svg/, sym + ": no misleading chart"); }
{ const f = figure(page("TWO"), "revenue"); eq(rects(f).map((r) => r.fy), [2023, 2026], "two values with a gap between them: drawn, with the two empty slots between"); eq([...f.matchAll(/>FY(\d\d)<\/text>/g)].length, 4, "four slots"); }
{ const d = DOC(); d.stocks.INFY.years.forEach((y) => { y.values.eps_basic = null; }); re(figure(page("INFY", d), "eps_basic"), /data-state="unavailable"/, "a metric with no values at all is unavailable"); eq(keysOf(page("INFY", d)).length, 5, "still five figures (one unavailable)"); }
{ const d = DOC(); d.stocks.INFY.years.forEach((y, i) => { if (i) y.values.eps_basic = null; }); re(figure(page("INFY", d), "eps_basic"), /data-state="unavailable"/, "one value only: unavailable"); }
for (const bad of [null, undefined, {}, { stocks: [] }, "x"]) { const o = F.build("TCS", bad); no(o, /<figure|<svg/, "no charts when the file is unavailable: " + JSON.stringify(bad)); re(text(o), /Financial history unavailable\./, "message unchanged"); }
no(page("NOPE"), /<figure|<svg/, "no charts for a stock that is not on file"); no(page("EMPTY", { stocks: { EMPTY: { symbol: "EMPTY", years: [] } } }), /<svg/, "no charts for a stock with no years");

// ================= 7. official-year hatching and the ITC divider =================
h = page("TCS"); { const f = figure(h, "revenue"); eq([...f.matchAll(/data-fy="(\d+)"[^>]*data-official="1"/g)].map((m) => +m[1]), [2022], "only the official FY2022 bar is hatched"); re(f, /<pattern id="fhHatch-\d+-revenue"/, "pattern defined in that chart");
  re(f, /fill="url\(#fhHatch-\d+-revenue\)"/, "the bar uses it"); re(text(h), /Hatched bars are from the official annual report\./, "key shown"); }
{ const ids = [...page("TCS").matchAll(/<pattern id="([^"]+)"/g)].map((m) => m[1]); eq(new Set(ids).size, ids.length, "pattern ids are unique within a panel"); const a = [...page("TCS").matchAll(/<pattern id="([^"]+)"/g)].map((m) => m[1]), b = [...page("TCS").matchAll(/<pattern id="([^"]+)"/g)].map((m) => m[1]); ok(a.every((x) => !b.includes(x)), "ids differ between two renders on one page (no clash when a stock is reopened)"); }
h = page("ITC");
for (const k of ["revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"]) { const f = figure(h, k); eq((f.match(/data-boundary="1"/g) || []).length, 1, "ITC " + k + ": one divider"); re(f, /Not compared across FY2022 to FY2023\./, "ITC " + k + ": caption");
  const rs = rects(f), line = +f.match(/<line data-boundary="1" x1="([\d.]+)"/)[1]; ok(rs[0].x + rs[0].w < line && line < rs[1].x, "ITC " + k + ": the divider sits between the FY2022 and FY2023 bars"); }
for (const s of ["TCS", "INFY", "OTHER", "HDFCBANK"]) { no(page(s), /data-boundary/, s + ": no divider"); no(page(s), /Not compared across/, s + ": no caption"); }
{ const d = DOC(); d.stocks.ITC.years = d.stocks.ITC.years.filter((y) => y.fy >= 2023); no(page("ITC", d), /data-boundary|Not compared/, "ITC without FY2022: nothing to divide"); }
{ const d = DOC(); d.stocks.ITC.years = d.stocks.ITC.years.filter((y) => y.fy !== 2023); const hh = page("ITC", d); no(figure(hh, "revenue"), /data-fy="2023"/, "ITC without FY2023: no FY2023 bar"); eq((figure(hh, "revenue").match(/data-boundary="1"/g) || []).length, 1, "the empty FY2023 slot is still separated from FY2022 by the divider"); }
re(page("ITC"), /data-official="1"/, "ITC FY2022 is hatched as official");

// ================= 8. no growth figures, no advice, no library, no network =================
for (const s of ["TCS", "ITC", "HDFCBANK", "NEG"]) { const hh = page(s), cs = hh.slice(hh.indexOf(">Charts<"), hh.indexOf('data-charts="1"') + 400 + hh.slice(hh.indexOf('data-charts="1"')).indexOf("</div>")); const grid = hh.match(/<div data-charts="1"[\s\S]*?<\/div>/)[0], t = text(grid);
  no(t, /%|CAGR|growth|change|YoY|trend ?line|moving average|forecast|projection|target|outlook|\b(buy|sell|hold|bullish|bearish|signal|rating|score|recommend)\b/i, s + ": the charts carry no growth figures, advice or signals");
  no(grid, /#[0-9a-fA-F]{3,8}\b/, s + ": no hard-coded colours (all from the page's CSS variables)"); ok(!/rgb\(|hsl\(/.test(grid), s + ": no colour functions"); ok(!/\b(red|green)\b/i.test(grid), s + ": no red/green semantics"); void cs; }
eq([...page("TCS").matchAll(/<figure data-chart="eps_basic"[\s\S]*?<\/figure>/g)].filter((m) => /without adjustment for splits or bonus issues/.test(m[0])).length, 1, "the EPS note is under the EPS chart");
for (const k of ["revenue", "profit_before_tax", "profit_after_tax", "operating_cash_flow"]) no(figure(page("TCS"), k), /splits or bonus/, k + ": the EPS note is not repeated");
const chartCode2 = fhCode.slice(fhCode.indexOf("Phase 5A: bar charts"), fhCode.indexOf("/* ---------- rendering ---------- */"));
ok(chartCode2.length > 500, "chart code located"); no(chartCode2, /fetch\(|XMLHttpRequest|createChart|LightweightCharts|unpkg|<script|document\.|window\.|localStorage|innerHTML|eval\(/, "the chart layer makes no request, loads no library, touches no DOM or storage");
eq((fhCode.match(/fetch\(/g) || []).length, 1, "the module still has exactly one fetch (the history file)"); eq([...new Set(fhCode.match(/out\/[a-z_]+\.json/g))], ["out/financial_history.json"], "and reads only that file");
eq(blocks.length, 7, "the seven classic script blocks are still seven"); eq(html.split(MOD).length, 2, "one module script");

// ================= 9. responsive / accessible markup =================
h = page("TCS"); ok(/<svg viewBox="0 0 320 196"/.test(figure(h, "revenue")), "SVG scales by viewBox"); re(figure(h, "revenue"), /style="display:block;width:100%;height:auto"/, "fills its cell, height follows the width");
re(h, /<div data-charts="1" style="display:grid;grid-template-columns:repeat\(auto-fit,minmax\(280px,1fr\)\);gap:14px">/, "grid wraps to one column on a phone");
re(figure(h, "revenue"), /role="img" aria-label="Revenue \(₹ crore\)\. FY2022 100; FY2023 110; FY2024 121; FY2025 133\.1; FY2026 146\.41\."/, "text alternative lists every year and value");
{ const hh = page("GAP"); re(figure(hh, "revenue"), /FY2025 not available/, "text alternative says a missing year is not available"); }
re(figure(h, "revenue"), /<title>FY2022: 100<\/title>/, "tooltip text on each bar (labels are always visible too, for touch)"); eq(Object.keys(labels(figure(h, "revenue"))).length, 5, "value labels are always printed");
ok(!/animation|transition|@keyframes/.test(chartCode2), "no animation");
{ const hh = page("LONG"); const f = figure(hh, "revenue"); eq(rects(f).length, 12, "12 years: all bars drawn"); eq(Object.keys(labels(f)).length, 0, "more than 8 years: value labels are dropped (they would collide), bars and axis remain"); re(f, /aria-label="Revenue \(₹ crore\)\. FY2015 100;/, "values stay available in the text alternative"); }
{ const f = figure(page("BIGBANK"), "total_revenue"), ys = [...f.matchAll(/<text data-label="\d+" x="[\d.]+" y="(-?[\d.]+)"/g)].map((m) => +m[1]); ok(new Set(ys.map((y) => y.toFixed(0))).size > 1, "long labels are staggered so neighbours do not overprint"); }
{ const f = figure(page("TCS"), "revenue"); const ys = [...f.matchAll(/<text data-label="\d+" x="[\d.]+" y="(-?[\d.]+)"/g)].map((m) => +m[1]); ok(ys.length === 5, "short labels"); }
ok(fhCode.includes('font-size="9"'), "chart text is 9 units on a 320-wide canvas (about 8.5px on a 300px phone column, 9px+ elsewhere)");
ok(!/\bwidth="\d{3,}"/.test(figure(h, "revenue")), "no fixed pixel width that could overflow a phone");

// ================= 10. escaping =================
{ const d = DOC(); d.stocks.TCS.years[0].fy = 2022; const o = page("TCS", d); no(o, /<script|onerror|javascript:/i, "no script or event handler in the output"); }

// ================= 11. the table, change rows and CAGR are unchanged from 3c07ada =================
let oldHtml = ""; try { oldHtml = cp.execSync("git show 3c07adab51eb82e5258c4971e83c8b5419bd3dd5:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { oldHtml = ""; }
if (oldHtml) {
  const O = load(modOf(oldHtml)), N = F, strip = (x) => { const a = x.indexOf('<h5 style="margin:16px 0 4px">Charts</h5>'); if (a < 0) return x; const g = x.indexOf('<div data-charts="1"', a), e = x.indexOf("</div>", g) + 6; return x.slice(0, a) + x.slice(e); };
  const d = DOC();
  for (const s of Object.keys(d.stocks).concat(["NOPE"])) eq(strip(N.build(s, d)), O.build(s, d), s + ": with the charts removed, the panel (table, change rows, CAGR lines, notes, states) is identical to the committed version");
  for (const bad of [null, {}, { stocks: [] }]) eq(N.build("TCS", bad), O.build("TCS", bad), "unavailable output is identical");
  for (const s of ["TCS", "ITC", "HDFCBANK", "GAP", "NEG"]) { const mn = N.model(d, s), mo = O.model(d, s); eq(JSON.stringify(mn), JSON.stringify(mo.metrics ? mo : mo), s + ": model is identical"); for (const x of mn.metrics) { eq(JSON.stringify(N.cagr(mn, x.key)), JSON.stringify(O.cagr(mo, x.key)), s + " " + x.key + " CAGR identical"); mn.cols.forEach((c, i) => eq(N.change(mn, i, x.key), O.change(mo, i, x.key), s + " " + x.key + " change[" + i + "] identical")); } }
  const hb = oldHtml.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
  eq(blocks.filter((b) => !b.includes("Stock Detail view")).map((b) => crypto.createHash("sha256").update(b).digest("hex")), hb.filter((b) => !b.includes("Stock Detail view")).map((b) => crypto.createHash("sha256").update(b).digest("hex")), "all classic script blocks except the Stock Detail block (Phase 5D.1) are byte-identical to the committed page");
  const outside = (x) => x.replace(/<script type="module" id="stocklens-compare">[\s\S]*?<\/script>\n/, "").replace(/<script type="module">[\s\S]*?<\/script>\n/, ""); const ND = ((x) => x.split("<script>").map((q, i) => (i && q.split("</script>")[0].includes("Stock Detail view") ? "</script>" + q.split("</script>").slice(1).join("</script>") : q)).join("<script>")); eq(ND(outside(html)), ND(outside(oldHtml)), "everything outside the module script (and the Stock Detail block, Phase 5D.1) is identical to the committed page");
}

// ================= 12. in the page: charts appear in the panel, nothing else moves =================
const dayStr = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
const candles = () => Array.from({ length: 400 }, (_, i) => ({ date: dayStr("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const bench = () => Array.from({ length: 400 }, (_, i) => ({ date: dayStr("2025-01-01", i), open: 20000 + i, high: 20001 + i, low: 19999 + i, close: 20000 + i, volume: 0 }));
const HIST = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: bench() }, stocks: Object.fromEntries(["TCS", "ITC", "HDFCBANK"].map((s) => [s, { symbol: s, candles: candles() }])) };
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: ["TCS", "ITC", "HDFCBANK"].map((s) => ({ symbol: s, company_name: s + " Ltd", sector: "Test", pe: 25.5, pb: 12.3, roa: 20.1, roe: 51.2, roce: 60.4, ev_ebitda: 18.7 })) };
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: ["TCS", "ITC", "HDFCBANK"].map((s) => ({ symbol: s, basis: "consolidated", income: { period: "Mar 2026", basis: "consolidated", revenue: 250000.5, total_revenue: 255000.25, profit_before_tax: 62000, profit_after_tax: 48000.75 }, balance_sheet: { period: "Mar 2026", total_assets: 150000, total_liabilities: 60000, total_equity: 90000 }, cash_flow: { period: "Mar 2026", operating: 55000, investing: -20000, financing: -30000, free_cash_flow: null } })) };
function stubLib(log) { const mk = (k) => { const s = { kind: k, data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; return s; };
  return { createChart(el, opts) { const ch = { el, opts, series: [], removed: false, addCandlestickSeries() { const s = mk("c"); ch.series.push(s); return s; }, addLineSeries() { const s = mk("l"); ch.series.push(s); return s; }, addHistogramSeries() { const s = mk("h"); ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }; }, remove() { ch.removed = true; } }; log.push(ch); return ch; } }; }
async function boot(hash, { fh = DOC(), extra = true } = {}) {
  const els = {}, win = {}, charts = [], fetched = [], observers = []; let boxHtml = "", children = [];
  const sched = () => queueMicrotask(() => observers.forEach((f) => f([])));
  const box = { id: "detail", get innerHTML() { return boxHtml + children.map((c) => c.innerHTML).join(""); }, set innerHTML(v) { boxHtml = v; children.forEach((c) => { c.parentNode = null; }); children = []; sched(); },
    appendChild(c) { c.parentNode = box; children.push(c); sched(); return c; }, insertBefore(c, ref) { c.parentNode = box; const i = ref ? children.indexOf(ref) : -1; if (i < 0) children.push(c); else children.splice(i, 0, c); sched(); return c; }, setAttribute() {}, addEventListener() {}, querySelector: () => null };
  const mk = (id) => { const e = { id, innerHTML: "", textContent: "", setAttribute() {}, addEventListener() {}, querySelector: () => null }; return e; };
  global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) observers.push(cb); }; };
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {}, LightweightCharts: stubLib(charts) };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => (i === "detail" ? box : children.find((c) => c.id === i) || els[i] || (els[i] = mk(i))), addEventListener() {}, querySelector: () => null,
    createElement: () => { const c = { parentNode: null, innerHTML: "", id: "" }; Object.defineProperty(c, "nextSibling", { get() { const i = children.indexOf(c); return i >= 0 ? children[i + 1] || null : null; } }); return c; }, head: { appendChild() {} } };
  const files = { "fundamentals.json": FUND, "financials.json": FIN, "historical.json": HIST, "financial_history.json": fh };
  global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()] ?? (u.endsWith("scans.json") ? { as_of: "2026-10-01", source: "NSE", stocks: [] } : null); return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(detailCode); eval(chartCode); eval(techCode); eval(researchCode); eval(checkCode); if (extra) eval(modOf(html));
  await sleep(80);
  const nav = async (h2) => { window.location.hash = h2; win.hashchange(); await sleep(80); };
  return { charts, fetched, nav, ids: () => children.map((c) => c.id), child: (id) => (children.find((c) => c.id === id) || {}).innerHTML || "", detailHtml: () => boxHtml };
}
(async () => {
  const base = await boot("#stock=TCS", { extra: false }); const bRes = base.child("detailResearch"), bChk = base.child("detailChecklist"), bDet = base.detailHtml();
  let t = await boot("#stock=TCS"); const fhp = t.child("detailFinancialHistory");
  eq(t.ids(), ["detailResearch", "detailFinancialHistory", "detailChecklist"], "order of sections unchanged: Stock Research, Financial History, Investment Checklist");
  eq(keysOf(fhp), ["revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "the page's panel shows the five charts"); eq(rects(figure(fhp, "revenue")).length, 5, "with five TCS bars");
  eq(t.child("detailResearch"), bRes, "Stock Research is identical with and without the module"); eq(t.child("detailChecklist"), bChk, "Investment Checklist identical"); eq(t.detailHtml(), bDet, "Stock Detail identical");
  ok(t.charts.length > 0 && t.charts.every((c) => !c.el || true), "the price chart is still drawn by the existing code"); eq(t.fetched.filter((u) => /financial_history/.test(u)).length, 1, "one request for the history file");
  ok(!t.fetched.some((u) => /unpkg|http/.test(u)), "no outside request"); eq(t.charts.length, base.charts.length, "the module created no Lightweight Charts chart of its own");
  await t.nav("#stock=ITC"); eq(t.ids(), ["detailResearch", "detailFinancialHistory", "detailChecklist"], "navigation replaces the panel"); re(t.child("detailFinancialHistory"), /data-boundary="1"/, "ITC page: the divider is drawn from the page's own file");
  await t.nav("#stock=HDFCBANK"); eq(keysOf(t.child("detailFinancialHistory"))[0], "total_revenue", "bank page: Total income chart first");
  t = await boot("#stock=TCS", { fh: null }); re(text(t.child("detailFinancialHistory")), /Financial history unavailable\./, "missing file: unavailable message"); no(t.child("detailFinancialHistory"), /<svg|<figure/, "no charts"); eq(t.ids().length, 3, "other sections intact"); re(t.child("detailChecklist"), /Investment Checklist/, "checklist intact");
  console.log("Financial history chart tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
