// Run: node tests/test_research.js   (no network, no browser; stubs the DOM, MutationObserver, fetch and the chart library)
// Tests the Phase 4 Step 1 Stock Research section: field mapping, N/A rules, missing data, reuse of the existing technical calculations,
// placement (Option B: appended to #detail, Stock Detail block untouched), routing, wording and block isolation.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research"));
const detailCode = blocks.find((b) => b.includes("Stock Detail view"));
const chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart"));
const techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot"));
assert.ok(researchCode && detailCode && chartCode && techCode, "script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- the existing calculation modules (loaded as pure APIs, to compare against) ----------
global.window = {}; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
const load = (code, name) => new Function(code.replace('"use strict";', "").replace("(function(){", "var window={};(function(){").replace(/\}\)\(\);\s*$/, "})();return window." + name + ";"))();
const S = load(chartCode, "SLChart"), T = load(techCode, "SLTech");

// ---------- fixtures (tests only) ----------
const weekdays = (n, start) => { const out = [], d = new Date(start + "T00:00:00Z"); while (out.length < n) { if (d.getUTCDay() % 6) out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 1); } return out; };
const walk = (n, p0, seed, start = "2023-01-02") => { let s = seed, p = p0; const rnd = () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296;
  return weekdays(n, start).map((date) => { const o = p, c = p * (1 + (rnd() - 0.48) * 0.03), h = Math.max(o, c) * 1.005, l = Math.min(o, c) * 0.995; p = c; return { date, open: +o.toFixed(2), high: +h.toFixed(2), low: +l.toFixed(2), close: +c.toFixed(2), volume: 100000 + Math.floor(rnd() * 50000) }; }); };
const day = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
// dataset A: one candle per calendar day, close = 100 + i (i = 0..399, last 2026-02-04 = 499); Nifty close = 20000 + i
const candlesA = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const benchA = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 20000 + i, high: 20001 + i, low: 19999 + i, close: 20000 + i, volume: 0 }));
const HIST = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: benchA() },
  stocks: { TCS: { symbol: "TCS", candles: candlesA() }, INFY: { symbol: "INFY", candles: walk(400, 1500, 23) }, TINY: { symbol: "TINY", candles: walk(10, 100, 5) } } };
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "Tata Consultancy Services Ltd", sector: "IT Services", pe: 25.5, pb: 12.3, roa: 20.1, roe: 51.2, roce: 60.4, ev_ebitda: 18.7, revenue_growth: null, profit_growth: null },
  { symbol: "INFY", company_name: "Infosys & Co <Ltd>", sector: null, pe: "abc", pb: 0, roa: null, roe: null, roce: null, ev_ebitda: null, revenue_growth: 77.77, profit_growth: 77.77 },
  { symbol: "HDFCBANK", company_name: "HDFC Bank Ltd", sector: "Financial Services", pe: 18.2, pb: 2.9, roa: 1.8, roe: 16.4, roce: null, ev_ebitda: null }] };
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TCS (financials name)", basis: "consolidated",
    income: { period: "Mar 2026", basis: "consolidated", revenue: 250000.5, total_revenue: 255000.25, profit_before_tax: 62000, profit_after_tax: 48000.75, eps_basic: 130.5, eps_diluted: 130.4,
      total_revenue_growth: 6.3, profit_before_tax_growth: 7.1, profit_after_tax_growth: 5.8, net_profit_growth: 99.99 },
    balance_sheet: { period: "Mar 2026", basis: "consolidated", total_assets: 150000, total_liabilities: 60000, total_equity: 90001, liabilities_to_equity: 1.2349, total_debt: 1234, debt_to_equity: 0.5 },
    cash_flow: { period: "Mar 2026", basis: "consolidated", operating: 55000, investing: -20000, financing: -30000, free_cash_flow: null, capex_status: "none" } },
  { symbol: "INFY", company_name: "Infosys Fin", basis: "standalone",
    income: { period: "Mar 2026", basis: "standalone", revenue: null, total_revenue: 0, profit_before_tax: null, profit_after_tax: -1234.5, total_revenue_growth: null, profit_after_tax_growth: -2.5, net_profit_growth: 77.77 },
    balance_sheet: {}, cash_flow: { operating: 100, investing: "x", financing: null, free_cash_flow: 12345.6, capex_status: "found" } },
  { symbol: "NONAME", basis: "consolidated", income: { period: "Mar 2026", basis: "consolidated", revenue: 5 }, balance_sheet: {}, cash_flow: {} }] };
const FILES = () => ({ "fundamentals.json": FUND, "financials.json": FIN, "historical.json": HIST });

// ---------- stub page: real Stock Detail + chart + snapshot + research scripts on a stub DOM with a MutationObserver ----------
function stubLib(log) {
  const mkSeries = (kind, opts) => { const s = { kind, opts, data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; return s; };
  return { createChart(el, opts) { const ch = { el, opts, series: [], removed: false,
    addCandlestickSeries(o) { const s = mkSeries("candle", o); ch.series.push(s); return s; }, addLineSeries(o) { const s = mkSeries("line", o); ch.series.push(s); return s; }, addHistogramSeries(o) { const s = mkSeries("hist", o); ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }; }, remove() { ch.removed = true; } }; log.push(ch); return ch; } };
}
async function boot(hash, files = FILES(), { delay = 0, observer = true, settle = null } = {}) {
  const els = {}, win = {}, charts = [], fetched = [], observers = [];
  let boxHtml = "", children = [];
  const sched = () => queueMicrotask(() => observers.forEach((f) => f([])));
  const box = { id: "detail",
    get innerHTML() { return boxHtml + children.map((c) => c.innerHTML).join(""); },
    set innerHTML(v) { boxHtml = v; children.forEach((c) => { c.parentNode = null; }); children = []; sched(); },
    appendChild(c) { c.parentNode = box; children.push(c); sched(); return c; }, setAttribute() {}, addEventListener() {}, querySelector: () => null };
  const mk = (id) => { const e = { id, innerHTML: "", textContent: "", attrs: {}, listeners: {}, setAttribute(k, v) { e.attrs[k] = v; }, addEventListener(t, f) { e.listeners[t] = f; }, querySelector: () => null }; return e; };
  if (observer) global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) observers.push(cb); }; }; else delete global.MutationObserver;
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {}, LightweightCharts: stubLib(charts) };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => (i === "detail" ? box : els[i] || (els[i] = mk(i))),
    addEventListener() {}, querySelector: () => null, createElement: () => ({ parentNode: null, innerHTML: "", id: "" }), head: { appendChild() {} } };
  global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); const f = files[u.split("/").pop()] ?? (u.endsWith("scans.json") ? { as_of: "2026-10-01", source: "NSE", stocks: [] } : null); return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(detailCode); eval(chartCode); eval(techCode); eval(researchCode);
  await sleep(settle ?? 60 + delay * 3);
  const nav = async (h, ms = 60 + delay * 3) => { window.location.hash = h; win.hashchange(); await sleep(ms); };
  return { els, charts, fetched, nav, win, children: () => children, count: () => children.length,
    page: () => (children.length ? children[children.length - 1].innerHTML : ""), detailHtml: () => boxHtml };
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const tile = (h, label) => { const m = h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + esc(label) + "</span></div>")); return m ? m[1] : "NOT FOUND"; };
const tilesOf = (h) => [...h.matchAll(/<div class="tile"><b[^>]*>([^<]*)<\/b><span>([^<]*)<\/span>/g)].map((m) => [m[2], m[1]]);
const panelOf = (h, title) => { const i = h.indexOf("<h4 style=\"margin:0 0 .2em\">" + title + "</h4>"); if (i < 0) return ""; const j = h.indexOf('<div class="panel"', i); return h.slice(i, j < 0 ? h.length : j); };
const liOf = (h, title) => { const m = h.match(new RegExp("<li[^>]*><b>" + esc(title) + "</b>([^]*?)</li>")); return m ? text(m[1]) : "NOT FOUND"; };

(async () => {
  // ================= 1. block structure and protection =================
  eq(blocks.length, 7, "seven script blocks: dashboard, detail, chart, snapshot, research, checklist, Phase 2B tabs");
  eq(blocks.indexOf(researchCode), 4, "research block sits after the Technical Snapshot block");
  eq(blocks.indexOf(techCode), 3, "Technical Snapshot block is just before it"); ok(blocks.indexOf(researchCode) < blocks.length - 1, "research block is before the final Phase 2B block");
  eq(sha(blocks[0]), "3ee2ef6f101ccd3cd0df60bbd0bd37008c977e49c02e0cb3ba9caf8d128e1e5c", "dashboard block byte-identical");
  eq(sha(detailCode), "ec1d3d5d5791202fea1aa3d77e1189bb7fce41533ed529d98e35998ec8a1de77", "Stock Detail block byte-identical (not modified)");
  eq(sha(chartCode), "ef5a348496d6aafa87c6352665fd3475d56d0bd778e73c062af759ec7b9dfc53", "chart + MACD block byte-identical");
  eq(sha(techCode), "5f187e38d73cb15eed203fbc0cc41deb7a88dce219c6eee6f55b6f99e719b339", "Technical Snapshot block byte-identical");
  eq(sha(blocks[blocks.length - 1]), "35de0d215ec3947da870f95e636f41bf4b130d6d929be4d91d3deb2661344d2f", "final Phase 2B block byte-identical");
  re(researchCode, /window\.SLResearch=/, "research module exposes its API");

  // ================= 2. placement (Option B) and layout =================
  let t = await boot("#stock=TCS"), h = t.page();
  eq(t.count(), 1, "exactly one Stock Research section is appended"); re(h, /<h3[^>]*>Stock Research<\/h3>/, "section is titled Stock Research");
  ok(t.detailHtml().includes('id="detailChart"') && t.detailHtml().includes('id="detailTech"'), "existing Stock Detail content (snapshot + chart containers) is intact");
  no(t.detailHtml(), /Stock Research/, "the Stock Detail markup itself contains no research section (appended separately)");
  re(t.detailHtml(), /Stock Detail: TCS/, "Stock Detail heading still rendered");
  const order = ["Business", "Fundamental Quality", "Financial Health", "Cash Flow", "Valuation", "Technical Summary", "Research Checklist"].map((x) => h.indexOf("<h4 style=\"margin:0 0 .2em\">" + x + "</h4>"));
  ok(order.every((x) => x > 0) && order.every((x, i) => i === 0 || x > order[i - 1]), "seven parts present in the planned order");
  ok(h.indexOf("Stock Research") < order[0], "heading before the first part");
  re(h, /This section gives no advice\./, "descriptive-only statement"); re(h, /"-" means a value is missing; "N\/A" means the data source does not provide it\./, "dash vs N/A explained");
  no(h, /class="(up|dn)"|var\(--up\)|var\(--dn\)/, "no green/red styling in the research section");

  // ================= 3. Business =================
  eq(tile(h, "Company name"), "Tata Consultancy Services Ltd", "company name from fundamentals.json"); eq(tile(h, "Sector"), "IT Services", "sector from fundamentals.json");
  eq(tile(h, "Business description"), "Unavailable", "business description is Unavailable (no source field exists)");
  no(panelOf(h, "Business"), /provides|offers|engaged in|leading|services to/i, "no invented description text");
  t = await boot("#stock=NONAME"); eq(tile(t.page(), "Company name"), "-", "no name anywhere -> -"); // NONAME is only in financials without a name
  const fb = FILES(); fb["fundamentals.json"] = { as_of: "2026-10-01", source: "Upstox", stocks: [{ symbol: "TCS", company_name: null, sector: null }] };
  t = await boot("#stock=TCS", fb); eq(tile(t.page(), "Company name"), "TCS (financials name)", "company name falls back to financials.json"); eq(tile(t.page(), "Sector"), "-", "missing sector -> -");
  t = await boot("#stock=INFY"); h = t.page(); eq(tile(h, "Company name"), "Infosys &amp; Co &lt;Ltd&gt;", "company name is HTML-escaped"); no(h, /<Ltd>/, "no raw markup from data reaches the page");

  // ================= 4. Fundamental Quality: stored values shown as stored =================
  t = await boot("#stock=TCS"); h = t.page();
  eq(tile(h, "Revenue"), "2,50,000.5", "Revenue"); eq(tile(h, "Total revenue"), "2,55,000.25", "Total revenue"); eq(tile(h, "Total revenue growth"), "+6.30%", "Total revenue growth (stored)");
  eq(tile(h, "Profit before tax"), "62,000", "Profit before tax"); eq(tile(h, "Profit after tax"), "48,000.75", "Profit after tax"); eq(tile(h, "Profit after tax growth"), "+5.80%", "PAT growth (profit_after_tax_growth)");
  eq(tile(h, "ROE %"), "51.20", "ROE from fundamentals.json"); eq(tile(h, "ROCE %"), "60.40", "ROCE"); eq(tile(h, "ROA %"), "20.10", "ROA");
  no(panelOf(h, "Fundamental Quality"), /99\.99/, "unverified net_profit_growth is never used");
  re(panelOf(h, "Fundamental Quality"), /Income statement: Mar 2026 · consolidated \(₹ crore\)/, "income period and basis shown"); re(panelOf(h, "Fundamental Quality"), /Fundamentals data: Upstox · Updated: 2026-10-01/, "ratio source and date shown");
  const nog = FILES(); nog["financials.json"] = JSON.parse(JSON.stringify(FIN)); nog["financials.json"].stocks[0].income.total_revenue_growth = null; nog["financials.json"].stocks[0].income.profit_after_tax_growth = null;
  t = await boot("#stock=TCS", nog); eq(tile(t.page(), "Total revenue growth"), "-", "growth missing -> - (never computed from revenue lines)"); eq(tile(t.page(), "Profit after tax growth"), "-", "PAT growth missing -> -");
  t = await boot("#stock=INFY"); h = t.page();
  eq(tile(h, "Total revenue"), "0", "a real zero is shown as 0, not -"); eq(tile(h, "Revenue"), "-", "null revenue -> -"); eq(tile(h, "Profit after tax"), "-1,234.5", "negative PAT shown with its sign");
  eq(tile(h, "Profit after tax growth"), "-2.50%", "negative growth"); eq(tile(h, "ROE %"), "-", "null ROE -> -"); no(panelOf(h, "Fundamental Quality"), /77\.77/, "net_profit_growth 77.77 not shown");

  // ================= 5. Financial Health: stored values; Debt / Equity always N/A =================
  t = await boot("#stock=TCS"); h = t.page();
  eq(tile(h, "Total assets"), "1,50,000", "Total assets"); eq(tile(h, "Total liabilities"), "60,000", "Total liabilities");
  eq(tile(h, "Total equity"), "90,001", "Total equity is the stored value (not recalculated as assets - liabilities = 90,000)");
  eq(tile(h, "Liabilities / Equity"), "1.23", "Liabilities / Equity is the stored value (not recalculated as 0.67)");
  eq(tile(h, "Debt / Equity"), "N/A", "Debt / Equity stays N/A even when the file carries a value"); no(panelOf(h, "Financial Health"), /1,234|0\.50/, "stored debt_to_equity / total_debt are never displayed");
  re(panelOf(h, "Financial Health"), /Liabilities \/ Equity is not Debt \/ Equity and is not comparable for banks/, "banks note kept");
  t = await boot("#stock=INFY"); eq(tile(t.page(), "Debt / Equity"), "N/A", "Debt / Equity N/A with an empty balance sheet"); eq(tile(t.page(), "Total assets"), "-", "empty balance sheet -> -");

  // ================= 6. Cash Flow: FCF N/A unless a real number =================
  t = await boot("#stock=TCS"); h = t.page();
  eq(tile(h, "Operating cash flow"), "55,000", "Operating"); eq(tile(h, "Investing cash flow"), "-20,000", "Investing"); eq(tile(h, "Financing cash flow"), "-30,000", "Financing");
  eq(tile(h, "Free cash flow"), "N/A", "FCF N/A when null (not operating + investing)"); no(panelOf(h, "Cash Flow"), /35,000|\b55,000 *- *20,000/, "FCF never estimated from operating and investing");
  t = await boot("#stock=INFY"); h = t.page(); eq(tile(h, "Free cash flow"), "12,345.6", "FCF shown when the file has a number"); eq(tile(h, "Investing cash flow"), "-", "non-numeric investing -> -"); eq(tile(h, "Financing cash flow"), "-", "null financing -> -");

  // ================= 7. Valuation =================
  t = await boot("#stock=TCS"); h = t.page(); eq(tile(h, "P/E"), "25.50", "P/E"); eq(tile(h, "P/B"), "12.30", "P/B"); eq(tile(h, "EV/EBITDA"), "18.70", "EV/EBITDA");
  t = await boot("#stock=INFY"); h = t.page(); eq(tile(h, "P/E"), "-", "non-numeric P/E -> -"); eq(tile(h, "P/B"), "0.00", "zero P/B shown as 0.00"); eq(tile(h, "EV/EBITDA"), "-", "null EV/EBITDA -> -"); no(panelOf(h, "Valuation"), /77\.77/, "revenue_growth / profit_growth placeholders are not used");
  t = await boot("#stock=HDFCBANK"); h = t.page(); eq(tile(h, "P/E"), "18.20", "bank P/E"); eq(tile(h, "ROCE %"), "-", "bank without ROCE -> -"); eq(tile(h, "EV/EBITDA"), "-", "bank without EV/EBITDA -> -");

  // ================= 8. Technical Summary (hand-calculated on dataset A) =================
  t = await boot("#stock=TCS"); h = t.page();
  eq(tile(h, "Last close (daily data)"), "₹499.00", "last close"); eq(tile(h, "Return 1M"), "+6.62%", "1M return (499/468-1)"); eq(tile(h, "Return 3M"), "+22.60%", "3M return"); eq(tile(h, "Return 6M"), "+58.41%", "6M return"); eq(tile(h, "Return 1Y"), "+272.39%", "1Y return");
  eq(tile(h, "52-week high"), "₹500.00", "52-week high"); eq(tile(h, "52-week low"), "₹133.00", "52-week low");
  eq(tile(h, "Close vs 20 DMA"), "+1.94%", "close vs 20 DMA"); eq(tile(h, "Close vs 50 DMA"), "+5.16%", "close vs 50 DMA"); eq(tile(h, "Close vs 200 DMA"), "+24.91%", "close vs 200 DMA");
  eq(tile(h, "RSI(14)"), "100.00", "RSI(14) of a straight rise = 100"); eq(tile(h, "MACD (12, 26, 9) line"), "7.00", "MACD of a unit ramp = 7"); eq(tile(h, "MACD 9-day EMA line"), "7.00", "MACD 9-day EMA line = 7"); eq(tile(h, "MACD histogram"), "0.00", "MACD histogram = 0.00 (never -0.00)");
  no(h, /-0\.00/, "no negative zero anywhere");
  eq(tile(h, "Relative Strength vs Nifty 50 (1Y ratio)"), ((499 / 134) / (20399 / 20034) * 100).toFixed(2), "RS ratio over the 1Y window: (499/134)/(20399/20034)x100");
  eq(tile(h, "Stock return in the 1Y window"), "+272.39%", "stock return in the RS window"); eq(tile(h, "Nifty 50 return in the 1Y window"), "+1.82%", "Nifty return in the RS window");
  re(panelOf(h, "Technical Summary"), /Daily data as of 2026-02-04\. Historical data is not live\./, "as-of date and not-live note"); re(panelOf(h, "Technical Summary"), /latest 1Y window/, "RS window clearly labelled 1Y");
  re(panelOf(h, "Technical Summary"), /same calculations as the Technical Snapshot and the chart/, "states that existing calculations are reused");
  // values equal the existing modules' own output on a random-walk history (wiring check)
  const WALK = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: walk(400, 20000, 7).map((c) => ({ ...c, volume: 0 })) }, stocks: { TCS: { symbol: "TCS", candles: walk(400, 3500, 11) } } };
  t = await boot("#stock=TCS", { ...FILES(), "historical.json": WALK }); h = t.page();
  const snap = T.compute(WALK, "TCS"), mdl = S.prepare(WALK, "TCS", "1Y"), f2 = (v) => v.toFixed(2), sg = (v) => (v > 0 ? "+" : "") + v.toFixed(2) + "%", lv = (a) => a[a.length - 1].value;
  eq(tile(h, "Last close (daily data)"), "₹" + new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(snap.lastClose), "last close equals SLTech.compute");
  eq([tile(h, "Return 1M"), tile(h, "Return 3M"), tile(h, "Return 6M"), tile(h, "Return 1Y")], ["1M", "3M", "6M", "1Y"].map((k) => sg(snap.returns[k])), "returns equal SLTech.compute");
  eq([tile(h, "Close vs 20 DMA"), tile(h, "Close vs 50 DMA"), tile(h, "Close vs 200 DMA")], [20, 50, 200].map((k) => sg(snap.dma[k])), "DMA comparisons equal SLTech.compute");
  eq([tile(h, "RSI(14)"), tile(h, "MACD (12, 26, 9) line"), tile(h, "MACD 9-day EMA line"), tile(h, "MACD histogram")], [f2(lv(mdl.rsi)), f2(lv(mdl.macd.line)), f2(lv(mdl.macd.signal)), f2(lv(mdl.macd.hist))], "RSI and MACD equal SLChart.prepare");
  eq([tile(h, "Relative Strength vs Nifty 50 (1Y ratio)"), tile(h, "Stock return in the 1Y window"), tile(h, "Nifty 50 return in the 1Y window")], [f2(mdl.rs.last), sg(mdl.rs.stockReturn), sg(mdl.rs.benchReturn)], "Relative Strength equals SLChart.prepare over 1Y");
  // fixed 1Y window: with 5 years of data the 5Y ratio differs, the research section still uses 1Y
  const LONG = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: walk(1305, 20000, 7, "2021-10-04").map((c) => ({ ...c, volume: 0 })) }, stocks: { TCS: { symbol: "TCS", candles: walk(1305, 3500, 13, "2021-10-04") } } };
  t = await boot("#stock=TCS", { ...FILES(), "historical.json": LONG }); h = t.page(); const r1 = S.prepare(LONG, "TCS", "1Y").rs, r5 = S.prepare(LONG, "TCS", "5Y").rs;
  ok(f2(r1.last) !== f2(r5.last), "fixture check: 1Y and 5Y ratios differ"); eq(tile(h, "Relative Strength vs Nifty 50 (1Y ratio)"), f2(r1.last), "with 5 years of data the section still uses the 1Y window");
  // the chart's own timeframe buttons do not change the research section
  const before = t.page(); t.els.detailChart.listeners.click({ type: "click", target: { getAttribute: (a) => (a === "data-tf" ? "5Y" : null) } }); await sleep(60); eq(t.page(), before, "changing the chart timeframe does not change Stock Research");
  // short and missing histories
  const SH = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: walk(31, 20000, 7) }, stocks: { TCS: { symbol: "TCS", candles: walk(31, 100, 4) } } };
  t = await boot("#stock=TCS", { ...FILES(), "historical.json": SH }); h = t.page(); const ms = S.prepare(SH, "TCS", "1Y");
  eq(tile(h, "RSI(14)"), f2(lv(ms.rsi)), "31 candles: RSI present"); eq(tile(h, "MACD (12, 26, 9) line"), f2(lv(ms.macd.line)), "31 candles: MACD line present");
  eq([tile(h, "MACD 9-day EMA line"), tile(h, "MACD histogram")], ["-", "-"], "31 candles: 9-day EMA line and histogram are - (never zero-filled)"); eq([tile(h, "Close vs 50 DMA"), tile(h, "Close vs 200 DMA")], ["-", "-"], "31 candles: 50/200 DMA -");
  eq([tile(h, "52-week high"), tile(h, "52-week low"), tile(h, "Return 1Y")], ["-", "-", "-"], "31 candles: 52-week range and 1Y return -");
  t = await boot("#stock=TINY"); h = t.page(); eq(tile(h, "RSI(14)"), "-", "10 candles: RSI -"); eq(tile(h, "Relative Strength vs Nifty 50 (1Y ratio)"), "-", "10 candles: RS -"); ok(tile(h, "Last close (daily data)").startsWith("₹"), "10 candles: last close still shown from SLTech.compute");
  const nob = JSON.parse(JSON.stringify(HIST)); delete nob.benchmark; t = await boot("#stock=TCS", { ...FILES(), "historical.json": nob }); eq(tile(t.page(), "Relative Strength vs Nifty 50 (1Y ratio)"), "-", "no benchmark -> RS -"); eq(tile(t.page(), "RSI(14)"), "100.00", "other technical values unaffected");
  t = await boot("#stock=TCS", { ...FILES(), "historical.json": null }); h = t.page(); ok(tilesOf(panelOf(h, "Technical Summary")).length === 17 && tilesOf(panelOf(h, "Technical Summary")).every((x) => x[1] === "-"), "no historical.json: all 17 technical tiles show -");

  // ================= 9. missing data never crashes and never invents values =================
  t = await boot("#stock=TCS", { "fundamentals.json": null, "financials.json": null, "historical.json": null }); h = t.page();
  ok(tilesOf(h).length > 40 && tilesOf(h).every((x) => ["-", "N/A", "Unavailable"].includes(x[1])), "no data files: every tile is -, N/A or Unavailable");
  no(text(h), /NaN|undefined|null|Infinity/, "no NaN / undefined / null text"); re(h, /No data was found for TCS in the current files\./, "explicit no-data message");
  re(liOf(h, "Business"), /Available: none\./, "checklist: nothing available"); re(h, /Fundamentals not loaded yet\./, "missing fundamentals reported"); re(h, /Financial statements not loaded yet\./, "missing financials reported");
  t = await boot("#stock=ZZZ"); h = t.page(); no(text(h), /NaN|undefined|null/, "unknown symbol: clean text"); re(h, /No data was found for ZZZ/, "unknown symbol: no-data message"); re(t.detailHtml(), /Stock Detail: ZZZ/, "unknown symbol: detail page still renders");
  t = await boot("#stock=TCS", { "fundamentals.json": FUND, "financials.json": null, "historical.json": null }); h = t.page(); eq(tile(h, "ROE %"), "51.20", "fundamentals only: ROE shown"); eq(tile(h, "Revenue"), "-", "fundamentals only: revenue -"); eq(tile(h, "Last close (daily data)"), "-", "fundamentals only: technical -");
  t = await boot("#stock=TCS", { "fundamentals.json": null, "financials.json": null, "historical.json": HIST }); h = t.page(); eq(tile(h, "Last close (daily data)"), "₹499.00", "history only: technical shown"); eq(tile(h, "P/E"), "-", "history only: P/E -");
  t = await boot("#stock=TCS", { "fundamentals.json": { stocks: "bad" }, "financials.json": { stocks: [null, 5, { symbol: "TCS", income: "x", balance_sheet: null, cash_flow: [] }] }, "historical.json": { stocks: "bad" } });
  ok(t.count() === 1 && !/NaN|undefined/.test(text(t.page())), "malformed files: section still renders, no crash");

  // ================= 10. Research Checklist (descriptive, available vs unavailable) =================
  t = await boot("#stock=TCS"); h = t.page();
  re(liOf(h, "Business"), /Available: Company name, Sector\. . Unavailable: Business description \(no verified business profile for this stock\)\./, "Business: description always listed as unavailable");
  re(liOf(h, "Financial Health"), /Unavailable: Debt \/ Equity \(no reliable debt data in the current data files\)\./, "Financial Health: Debt / Equity always unavailable");
  re(liOf(h, "Financial Health"), /Available: Total assets, Total liabilities, Total equity, Liabilities \/ Equity\./, "Financial Health: available list");
  re(liOf(h, "Cash Flow"), /Unavailable: Free cash flow \(no unambiguous capital-expenditure line\)\./, "Cash Flow: FCF unavailable when null"); re(liOf(h, "Cash Flow"), /Available: Operating cash flow, Investing cash flow, Financing cash flow\./, "Cash Flow: available list");
  re(liOf(h, "Fundamental Quality"), /Available: Revenue, Total revenue, Total revenue growth, Profit before tax, Profit after tax, Profit after tax growth, ROE %, ROCE %, ROA %\. . Unavailable: none\./, "Quality: everything available for TCS");
  re(liOf(h, "Valuation"), /Available: P\/E, P\/B, EV\/EBITDA\./, "Valuation available"); re(liOf(h, "Technical Summary"), /Unavailable: none\./, "Technical: all available on a long history");
  t = await boot("#stock=INFY"); h = t.page();
  re(liOf(h, "Cash Flow"), /Available: Operating cash flow, Free cash flow\. . Unavailable: Investing cash flow, Financing cash flow\./, "Cash Flow: FCF available when a number exists; others listed unavailable");
  re(liOf(h, "Fundamental Quality"), /Unavailable: Revenue, Profit before tax, Total revenue growth, ROE %, ROCE %, ROA %|Unavailable: .*Revenue/, "INFY: missing items are listed as unavailable"); re(liOf(h, "Valuation"), /Available: P\/B\. . Unavailable: P\/E, EV\/EBITDA\./, "INFY valuation list");
  t = await boot("#stock=TINY"); re(liOf(t.page(), "Technical Summary"), /Available: Last close \(daily data\)\./, "TINY: only last close is available among technical items");

  // ================= 11. routing, observer behaviour, stale paint =================
  t = await boot("#stock=TCS"); eq(t.count(), 1, "one section after load (the observer's own append does not add another)"); re(t.page(), /Tata Consultancy Services Ltd/, "TCS research shown");
  await t.nav("#stock=INFY"); eq(t.count(), 1, "navigating to INFY replaces the section (still exactly one)"); eq(tile(t.page(), "Company name"), "Infosys &amp; Co &lt;Ltd&gt;", "INFY research shown after navigation"); no(t.page(), /Tata Consultancy/, "no TCS data left on the INFY page");
  await t.nav("#stock=TCS"); eq(tile(t.page(), "Company name"), "Tata Consultancy Services Ltd", "back to TCS: TCS research again"); eq(t.count(), 1, "still one section");
  await t.nav(""); const fetchesBefore = t.fetched.length; ok(t.count() <= 1, "leaving the stock adds no new section"); eq(t.fetched.length, fetchesBefore, "leaving the stock makes no request");
  t = await boot(""); eq(t.count(), 0, "dashboard: no research section"); ok(!t.fetched.some((u) => /historical|financials|fundamentals/.test(u)) || t.fetched.length === 0, "dashboard: no research data requests from the research module");
  t = await boot("#stock=TCS", FILES(), { delay: 100, settle: 150 }); re(t.page(), /Loading/, "fixture check: research of TCS is still loading"); await t.nav("#stock=INFY", 500);
  eq(t.count(), 1, "slow files, quick navigation: one section"); eq(tile(t.page(), "Company name"), "Infosys &amp; Co &lt;Ltd&gt;", "slow files, quick navigation: the section shows the stock currently in the hash");
  t = await boot("#stock=TCS", FILES(), { delay: 100, settle: 150 }); re(t.page(), /Loading/, "fixture check: research still loading"); await t.nav("", 500); no(t.page(), /Tata Consultancy/, "leaving before the data arrives: no stale research is painted"); re(t.page(), /Loading/, "...the old placeholder is left untouched");
  t = await boot("#stock=TCS", FILES(), { observer: false }); ok(true, "without MutationObserver the page does not crash"); eq(t.count(), 0, "without MutationObserver nothing is appended (the section needs it)");

  // ================= 12. wording =================
  const BAD = /\b(buy|sell|bullish|bearish|signal|recommend\w*|target|strong|weak|undervalued|overvalued|good|poor|score|rating)\b/i;
  for (const sym of ["TCS", "INFY", "HDFCBANK", "TINY", "ZZZ"]) { t = await boot("#stock=" + sym); no(text(t.page()), BAD, "no advice / signal / rating wording in the " + sym + " research text"); }
  no(text(panelOf(t.page(), "Technical Summary")), /interpret|trend|momentum|overbought|oversold|crossover/i, "RSI and MACD are numbers only: no interpretation words");
  no(researchCode.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\.signal\b/g, ""), BAD, "no such words in the research code (the chart model's existing .signal property name is the only exception)");

  // ================= 13. security / scope =================
  const code = researchCode.replace(/\/\*[\s\S]*?\*\//g, "");
  no(code, /UPSTOX_ANALYTICS_TOKEN|Bearer\s|Authorization|api\.upstox\.com|upstox\.com/i, "no token, auth header or Upstox host"); no(code, /WebSocket|EventSource|setInterval|XMLHttpRequest|localStorage|sessionStorage/, "no live data, polling or storage");
  eq((code.match(/fetch\(/g) || []).length, 1, "the research module has exactly one fetch call (a helper)"); eq([...new Set(code.match(/out\/[a-z_]+\.json/g))].sort(), ["out/company_profiles.json", "out/financials.json", "out/fundamentals.json", "out/historical.json"], "it reads only the four data files (three existing + company_profiles.json)");
  t = await boot("#stock=TCS"); ok(t.fetched.every((u) => /^out\/(fundamentals|financials|historical|scans|company_profiles)\.json$/.test(u)), "the whole page requests only existing JSON files: " + [...new Set(t.fetched)].join(", ")); ok(!t.fetched.some((u) => /upstox/i.test(u)), "browser never calls Upstox");
  no(html, /UPSTOX_ANALYTICS_TOKEN|Bearer\s|Authorization/i, "no token or auth header anywhere in the page"); no(html, /api\.upstox\.com/, "no Upstox call anywhere in the page");
  ok(!fs.existsSync(__dirname + "/../out/historical.json"), "no fake out/historical.json is shipped"); ok(!fs.existsSync(__dirname + "/../out"), "no new data files shipped");

  console.log("Research tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
