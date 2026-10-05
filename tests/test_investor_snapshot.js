// Run: node tests/test_investor_snapshot.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Phase 5E Investor Snapshot: a compact summary placed in the Stock Detail view directly before its Overview section.
// It must only ARRANGE existing values (price and day change from the comparison page's rule, 1M-5Y returns from the Phase 5D rule, business / ratios / balance sheet / cash flow / valuation
// from Stock Research, growth and CAGR from Financial History) and never invent a value: no margin, no EPS growth, no free cash flow arithmetic, no debt / equity from liabilities,
// no ownership, no order or capex data. It must appear on Stock Detail only, fetch each existing file at most once, and leave the dashboard and the comparison page exactly as they were.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">', SNAPTAG = '<script type="module" id="stocklens-snapshot">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const detailCode = blocks.find((b) => b.includes("Stock Detail view")), techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot")),
      researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research")), fhCode = modOf(html, FHTAG), cmpCode = modOf(html, CMPTAG), snapCode = modOf(html, SNAPTAG);
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const near = (a, b, tol, m) => { checks++; assert.ok(Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const un = (s) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();
ok(snapCode.length > 1000 && detailCode && techCode && researchCode && fhCode && cmpCode, "all the page's scripts are found");

// ---------- independent restatements of the formats ----------
const N2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }), N0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const f2 = (v) => N2.format(v), f0 = (v) => N0.format(v);
const sp = (v) => { if (v === null || v === undefined || !isFinite(v)) return "-"; let s = v.toFixed(2); if (s === "-0.00") s = "0.00"; return (v > 0 ? "+" : "") + s + "%"; };

// ---------- the stubbed page ----------
const OV = '<h3 style="margin:1.4em 0 .4em">Overview</h3>';
const STATIC_VIEW = (s) => '<p><button class="lk" id="detailBack" type="button">← Back to StockLens</button></p><h2 style="margin-top:.6em">Stock Detail: ' + s + '</h2><div class="tiles"><div class="tile"><b>x</b><span>Symbol</span></div></div><p class="note">price note</p>' + OV + '<div class="tiles"></div><h3>Financial Health</h3><div id="detailTech"></div><h3>Price chart</h3><div class="panel" id="detailChart">chart</div>';
function load({ hash = "", files = {}, delay = 0, full = false, drop = [] } = {}) {
  const fetched = [], obs = [], win = {}, listeners = {}, els = {}, styles = [];
  let before = "", inserted = [], atEnd = new Set();
  const notify = () => setTimeout(() => obs.forEach((f) => f()), 0);
  const box = { id: "detail", parentNode: null,
    get innerHTML() { const i = before.indexOf(OV), a = inserted.filter((n) => !atEnd.has(n)).map((n) => n.innerHTML).join(""), z = inserted.filter((n) => atEnd.has(n)).map((n) => n.innerHTML).join(""); return (i < 0 ? before + a : before.slice(0, i) + a + before.slice(i)) + z; },
    set innerHTML(v) { before = v; inserted.forEach((n) => { n.parentNode = null; }); inserted = []; atEnd.clear(); notify(); },
    get children() { return before.includes(OV) ? [{ tagName: "H3", textContent: "Overview" }] : []; },
    insertBefore(el) { el.parentNode = box; inserted.push(el); notify(); return el; }, appendChild(el) { el.parentNode = box; inserted.push(el); atEnd.add(el); notify(); return el; },
    setAttribute() {}, addEventListener() {}, querySelector: () => null };
  const mk = (id) => ({ id, innerHTML: "", textContent: "", scrolled: null, setAttribute() {}, addEventListener() {}, querySelector: () => null, scrollIntoView(o) { this.scrolled = o; } });
  global.MutationObserver = undefined;
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, head: { appendChild: (e) => styles.push(e) },
    getElementById: (i) => (i === "detail" ? box : i === "snapshotStyle" ? styles.find((x) => x.id === "snapshotStyle") || null : els[i] || (els[i] = mk(i))),
    addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); }, querySelector: () => null,
    createElement: (tag) => ({ tag, id: "", innerHTML: "", textContent: "", parentNode: null, setAttribute() {} }) };
  global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); const f = files[u.split("/").pop()]; return { ok: f !== undefined && f !== null, json: async () => JSON.parse(JSON.stringify(f)) }; };
  (0, eval)(techCode); (0, eval)(researchCode); (0, eval)(fhCode); (0, eval)(cmpCode);
  for (const d of drop) delete window[d];
  if (full) (0, eval)(detailCode);
  global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) obs.push(cb); }; };
  (0, eval)(snapCode);
  const P = {
    box, fetched, win, listeners, styles, els, S: window.SLSnapshot,
    node: () => inserted.filter((n) => n.id === "detailSnapshot"), nodes: () => inserted,
    page: () => box.innerHTML,
    snap: () => { const n = inserted.filter((x) => x.id === "detailSnapshot")[0]; return n ? n.innerHTML : ""; },
    show: async (sym, ms = 40) => { window.location.hash = "#stock=" + sym; box.innerHTML = STATIC_VIEW(sym); await sleep(ms); },
    nav: async (h, ms = 60) => { window.location.hash = h; if (win.hashchange) win.hashchange(); await sleep(ms); } };
  return P;
}

// ---------- fixtures ----------
const cd = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const dayAdd = (ds, n) => { const d = new Date(ds + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const between = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
const START = "2021-01-01", END = "2026-10-02";
const closeOn = (d) => 100 + between(START, d);
const LONG = []; for (let d = START; d <= END; d = dayAdd(d, 1)) LONG.push(cd(d, closeOn(d)));
const CUT = { "1M": "2026-09-02", "3M": "2026-07-02", "6M": "2026-04-02", "1Y": "2025-10-02", "3Y": "2023-10-02", "5Y": "2021-10-02" }, PER = Object.keys(CUT);
const expRet = (p) => (closeOn(END) / closeOn(CUT[p]) - 1) * 100;
const PRICES = (over = {}) => ({ updated: END, source: "Upstox", stocks: Object.assign({
  TCS: { symbol: "TCS", candles: LONG.slice().reverse() },                                            // unsorted on purpose
  HDFCBANK: { symbol: "HDFCBANK", candles: [cd("2026-10-01", 1500), cd("2026-10-02", 1455)] },       // two days only
  INFY: { symbol: "INFY", candles: [cd("2026-10-02", 1500)] },                                      // one candle
  FLAT: { symbol: "FLAT", candles: [cd("2026-09-01", 50), cd("2026-10-01", 50), cd("2026-10-02", 50)] } }, over) });
const VAL = () => ({ as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TCS LIMITED", sector: "IT Services", pe: 25.5, pb: 12, roe: 45, roce: 55, roa: 20, ev_ebitda: 18 },
  { symbol: "HDFCBANK", company_name: "HDFC BANK LTD", sector: "Bank", pe: 18, pb: 3, roe: 16, roce: null, roa: 2, ev_ebitda: null },
  { symbol: "INFY", company_name: "INFY LIMITED", sector: null, pe: null, pb: null, roe: null, roce: null, roa: null, ev_ebitda: null },
  { symbol: "FLAT", company_name: "FLAT LTD", sector: "X", pe: 1, pb: 1, roe: 1, roce: 1, roa: 1, ev_ebitda: 1 }] });
const FINS = (sym, o = {}) => Object.assign({ symbol: sym, company_name: sym + " LIMITED", basis: "consolidated",
  income: { period: "Mar 2025", basis: "consolidated", revenue: 1000000, total_revenue: 1010000, profit_before_tax: 200000, profit_after_tax: 150000, eps_basic: 41.5, eps_diluted: 41.4, operating_margin: 33, ebitda_margin: 30 },
  balance_sheet: { period: "Mar 2025", basis: "consolidated", total_assets: 5000, total_liabilities: 3000, total_equity: 2000, liabilities_to_equity: 1.5, total_debt: null, debt_to_equity: null },
  cash_flow: { period: "Mar 2025", basis: "consolidated", operating: 400, investing: -300, financing: -50, free_cash_flow: null } }, o);
const FIN = () => ({ as_of: "2026-10-01", source: "Upstox", stocks: [FINS("TCS"), FINS("HDFCBANK"), FINS("INFY", { balance_sheet: { period: "Mar 2025", basis: "consolidated", total_assets: null, total_liabilities: null, total_equity: null, liabilities_to_equity: null, total_debt: null, debt_to_equity: null }, cash_flow: { period: "Mar 2025", basis: "consolidated", operating: null, investing: null, financing: null, free_cash_flow: null } })] });
const yrs = (byFy) => Object.keys(byFy).map((fy) => ({ fy: +fy, basis: "consolidated", values: byFy[fy], source: { provider: "Upstox" }, verification: {} }));
const FH = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox", stocks: {
  TCS: { statement_layout: "standard", years: yrs({
    2022: { revenue: 1000, total_revenue: 2000, profit_before_tax: 200, profit_after_tax: 150, eps_basic: 8, operating_cash_flow: 100 },
    2023: { revenue: 1100, total_revenue: 2200, profit_before_tax: 220, profit_after_tax: 165, eps_basic: 9, operating_cash_flow: 110 },
    2024: { revenue: 1210, total_revenue: 2420, profit_before_tax: 250, profit_after_tax: 180, eps_basic: 10, operating_cash_flow: 120 },
    2025: { revenue: 1331, total_revenue: 2662, profit_before_tax: 280, profit_after_tax: 200, eps_basic: 11, operating_cash_flow: 130 },
    2026: { revenue: 1500, total_revenue: 3000, profit_before_tax: 300, profit_after_tax: 240, eps_basic: 12.5, operating_cash_flow: 140 } }) },
  HDFCBANK: { statement_layout: "financial", years: yrs({
    2023: { revenue: null, total_revenue: 4000, profit_before_tax: 900, profit_after_tax: 700, eps_basic: 20, operating_cash_flow: 1 },
    2024: { revenue: null, total_revenue: 4400, profit_before_tax: 950, profit_after_tax: 740, eps_basic: 22, operating_cash_flow: 1 },
    2025: { revenue: null, total_revenue: 5000, profit_before_tax: 1000, profit_after_tax: 800, eps_basic: 24, operating_cash_flow: 1 },
    2026: { revenue: null, total_revenue: 5500, profit_before_tax: 1100, profit_after_tax: 880, eps_basic: 26, operating_cash_flow: 1 } }) },
  ZED: { statement_layout: "standard", years: yrs({
    2025: { revenue: 500, total_revenue: 5, profit_before_tax: 50, profit_after_tax: 40, eps_basic: 3, operating_cash_flow: 1 },
    2026: { revenue: null, total_revenue: 6, profit_before_tax: null, profit_after_tax: 45, eps_basic: null, operating_cash_flow: 1 } }) },
  INFY: { statement_layout: "standard", years: yrs({
    2024: { revenue: 800, total_revenue: 810, profit_before_tax: 100, profit_after_tax: 80, eps_basic: 5, operating_cash_flow: 9 },
    2025: { revenue: null, total_revenue: 900, profit_before_tax: null, profit_after_tax: 90, eps_basic: null, operating_cash_flow: 9 },
    2026: { revenue: 1000, total_revenue: 1010, profit_before_tax: 120, profit_after_tax: 100, eps_basic: 6, operating_cash_flow: 9 } }) } } });
const PROF = () => ({ as_of: "2026-10-04", stocks: [
  { symbol: "TCS", company_name: "Profile TCS", sector: "Profile Sector", description: "Provides technology services to enterprises.", key_businesses: ["IT services", "Consulting"], business_model: null, source_name: "Annual report", source_url: "https://example.com/tcs", source_date: "2025-05-01", retrieved_on: "2026-10-04", evidence_quote: "q" },
  { symbol: "HDFCBANK", company_name: "HDFC", sector: "Bank", description: "LEAK-DESCRIPTION should never be shown", key_businesses: ["LEAK-KEY"], business_model: null, source_name: "Report", source_url: null, source_date: null, retrieved_on: "2026-10-04", evidence_quote: "q" }] });
const D = (o = {}) => Object.assign({ val: VAL(), fin: FIN(), fh: FH(), prices: PRICES(), prof: PROF() }, o);
const FILES = (d) => ({ "fundamentals.json": d.val, "financials.json": d.fin, "financial_history.json": d.fh, "historical.json": d.prices, "company_profiles.json": d.prof });

// ---------- reading the output ----------
const grp = (h, id) => { const i = h.indexOf('data-snap="' + id + '"'); if (i < 0) return ""; let j = h.indexOf('data-snap="', i + 10); if (j >= 0) j = h.lastIndexOf('<div class="snap-g', j); return h.slice(i, j < 0 ? h.length : j); };
const items = (g) => [...g.matchAll(/<div class="snap-i"><span>(.*?)<\/span><b(?: class="([^"]*)")?>(.*?)<\/b><\/div>/gs)].map((m) => ({ l: un(m[1]), c: m[2] || "", v: un(m[3]) }));
const val = (h, id, l) => { const it = items(grp(h, id)).find((x) => x.l === l); return it ? it.v : "NOT FOUND"; };
const cls = (h, id, l) => { const it = items(grp(h, id)).find((x) => x.l === l); return it ? it.c : "NOT FOUND"; };
const rowOf = (h, name) => { const g = grp(h, "growth"); const parts = g.split('<div class="snap-r">').slice(1); const p = parts.find((x) => un(x.match(/<p class="snap-rl">(.*?)<\/p>/s)[1]) === name); return p ? items(p) : null; };
const rowNames = (h) => grp(h, "growth").split('<div class="snap-r">').slice(1).map((x) => un(x.match(/<p class="snap-rl">(.*?)<\/p>/s)[1]));
const ids = (h) => [...h.matchAll(/data-snap="([a-z]+)"/g)].map((m) => m[1]);
const ADVICE = /\b(buy|sell|hold|strong|bullish|bearish|score|scores|rating|rated|rank|ranks|ranking|ranked|winner|loser|best|worst|top|target|signal|recommend\w*|outperform\w*|underperform\w*|undervalued|overvalued|cheap|expensive|attractive|upside|downside|should|must|opportunit\w*|multibagger|safe)\b/i;

(async () => {
  // ================= 1. structure: the page is protected, the snapshot is a separate module =================
  eq(blocks.length, 7, "still exactly seven classic script blocks"); eq(html.split(FHTAG).length, 2, "one Financial History module"); eq(html.split(CMPTAG).length, 2, "one compare module"); eq(html.split(SNAPTAG).length, 2, "one snapshot module, with its own tag");
  eq((html.match(/<script/g) || []).length, 11, "eleven script elements in all (Phase 5F adds the search module)");
  ok(html.indexOf(CMPTAG) < html.indexOf(SNAPTAG), "the snapshot module comes after the modules it reads (Financial History, comparison)");
  let base = ""; try { base = cp.execSync("git show f7e8d14:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
  if (base) {
    const ob = base.split("<script>").slice(1).map((x) => x.split("</script>")[0]);
    eq(blocks.map(sha), ob.map(sha), "the seven classic blocks are byte-identical to f7e8d14 (Phase 5D.1), Stock Detail included");
    eq(sha(fhCode), sha(modOf(base, FHTAG)), "the Financial History module is byte-identical to f7e8d14");
    eq(cmpCode.replace("window.SLCompare={priceFor:priceFor,", "window.SLCompare={"), modOf(base, CMPTAG), "the comparison module differs from f7e8d14 only by one added export name, priceFor");
    const rest = html.replace(/<script type="module" id="stocklens-snapshot">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, "").replace("window.SLCompare={priceFor:priceFor,", "window.SLCompare={");
    eq(rest, base, "the page without the snapshot module (and that one export) is identical to f7e8d14: dashboard, Stock Detail, comparison all untouched");
  }

    const code = snapCode.replace(/\/\*[\s\S]*?\*\//g, "");
// ================= 2. what it calculates: nothing, and where it reads from =================
  no(code, /scans/, "no scans.json (or any scan list) in the snapshot code"); eq((code.match(/fetch\(/g) || []).length, 1, "one fetch call");
  eq([...new Set(code.match(/out\/[a-z_]+\.json/g))].sort(), ["out/company_profiles.json", "out/financial_history.json", "out/financials.json", "out/fundamentals.json", "out/historical.json"], "only the five files the page already uses; no new file");
  no(code, /localStorage|sessionStorage|indexedDB|XMLHttpRequest|eval\(|document\.write|https?:|new Date|Date\.now|Math\.random|setInterval|WebSocket|\.sort\(|\.reverse\(/, "no storage, network address, clock, randomness, sorting or reordering");
  no(code, /\.operating|\.investing|free_cash_flow\s*[-+*]|capex\b.*[-+]/, "free cash flow is never built from operating and investing cash flow");
  no(code, /total_liabilities|total_equity|total_assets/, "debt / equity is never derived from the balance-sheet totals");
  re(code, /debt_to_equity/, "debt / equity is read only from its stored field");
  no(code, /margin_|ebitda_margin|operating_margin|profit_margin|\bmargin\s*=/, "no margin is read or built");
  no(code, /eps[_a-z]*\s*(growth|change)|F\.change\([^)]*eps|\.eps/, "no EPS growth");
  no(code, /priceFor\s*=|function priceFor|function performance|\.candles|\.close\b/, "no second price calculation: the price code is the comparison page's");
  no(code, /<table/, "no table in the snapshot (compact rows, no horizontal scrolling)");
  no(code, /class="tile"|\.tiles/, "not the big tile cards of the detail sections");

  // ================= 3. the output with real-looking data: TCS (non-bank) =================
  const d = D();
  let t = load(); let h = t.S.build("TCS", d); const tx = un(h);
  eq(ids(h), ["price", "business", "growth", "profitability", "health", "cash", "valuation", "future", "ownership", "developments"], "the groups, in the order asked for");
  re(h, /<h3[^>]*>Investor Snapshot<\/h3>/, "titled Investor Snapshot"); eq((h.match(/<table/g) || []).length, 0, "no table"); no(h, /class="tile"/, "no big tiles");
  ok((h.match(/class="snap-i"/g) || []).length <= 40, "compact: at most 40 small value cells in all");
  // --- price & performance: independent numbers
  eq(val(h, "price", "Latest Price (last close, ₹)"), f2(closeOn(END)), "latest price = the newest valid close (the candles were given newest first)");
  eq(val(h, "price", "Day change vs previous close"), sp((closeOn(END) / closeOn("2026-10-01") - 1) * 100), "day change against the immediately preceding close");
  for (const p of PER) eq(val(h, "price", p + " return"), sp(expRet(p)), p + " return = close on " + END + " against the close on or before " + CUT[p]);
  re(tx, /Last daily close as of 2026-10-02 \(not a live price\)/, "the candle date is shown and it says the price is not live"); no(tx, /\blive LTP\b/i, "never called live LTP");
  eq(cls(h, "price", "1Y return"), "up", "a rise is styled as a rise");
  // --- business
  eq(val(h, "business", "Company"), "TCS LIMITED", "company from fundamentals"); eq(val(h, "business", "Sector"), "IT Services", "sector from fundamentals");
  re(h, /Provides technology services to enterprises\./, "business description from company_profiles.json"); re(h, /<li>IT services<\/li>/, "key businesses"); re(h, /<a href="https:\/\/example\.com\/tcs"[^>]*>Annual report<\/a>/, "official source attribution as a link");
  re(tx, /Source date: 2025-05-01/, "source date"); re(tx, /Retrieved: 2026-10-04/, "retrieved date");
  // --- growth: non-bank uses Revenue, never Total income
  eq(rowNames(h), ["Revenue", "Profit before tax", "Profit after tax", "EPS, basic (₹)"], "non-bank: Revenue (not Total income), PBT, PAT, EPS basic");
  let r = rowOf(h, "Revenue"); eq(r.map((x) => x.l), ["FY2026", "vs previous year", "4-yr CAGR (FY22–FY26)"], "revenue row labels"); eq(r[0].v, f0(1500), "latest revenue is the Revenue field (1,500), not Total income (3,000)");
  near(parseFloat(r[1].v), (1500 - 1331) / 1331 * 100, 0.006, "latest-year revenue growth"); eq(r[1].v, sp((1500 - 1331) / 1331 * 100), "growth string"); eq(r[2].v, sp((Math.pow(1500 / 1000, 1 / 4) - 1) * 100), "revenue CAGR over FY22-FY26 (4 years)");
  r = rowOf(h, "Profit before tax"); eq([r[0].v, r[1].v, r[2].v], [f0(300), sp((300 - 280) / 280 * 100), sp((Math.pow(300 / 200, 1 / 4) - 1) * 100)], "PBT: latest, growth, CAGR");
  r = rowOf(h, "Profit after tax"); eq([r[0].v, r[1].v, r[2].v], [f0(240), sp(20), sp((Math.pow(240 / 150, 1 / 4) - 1) * 100)], "PAT: latest, growth, CAGR");
  r = rowOf(h, "EPS, basic (₹)"); eq(r.length, 1, "EPS has one cell only: as reported, no growth, no CAGR"); eq(r[0].v, f2(12.5), "EPS basic as reported"); no(grp(h, "growth").replace(/<p class="snap-n">[^]*?<\/p>/, ""), /EPS[^]*?(growth|CAGR)/i, "no EPS growth or CAGR in the rows");
  re(un(grp(h, "growth")), /EPS growth is not calculated/, "and it says so");
  // --- profitability
  eq([val(h, "profitability", "ROE %"), val(h, "profitability", "ROCE %"), val(h, "profitability", "ROA %")], [f2(45), f2(55), f2(20)], "ROE, ROCE, ROA from fundamentals");
  eq(val(h, "profitability", "Margin"), "Unavailable", "margin: Unavailable"); no(un(grp(h, "profitability")), /33|30\.00|30%/, "the operating_margin / ebitda_margin decoy fields in the data are not shown: no margin is read or built");
  // --- financial health
  eq([val(h, "health", "Total assets (₹ cr)"), val(h, "health", "Total liabilities (₹ cr)"), val(h, "health", "Total equity (₹ cr)"), val(h, "health", "Liabilities / Equity")], ["5,000", "3,000", "2,000", "1.50"], "balance sheet values as stored");
  eq(val(h, "health", "Debt / Equity"), "N/A", "no stored debt / equity: N/A (not 1.50, not 3000/2000)");
  // --- cash flow
  eq([val(h, "cash", "Operating (₹ cr)"), val(h, "cash", "Investing (₹ cr)"), val(h, "cash", "Financing (₹ cr)")], ["400", "-300", "-50"], "operating, investing, financing as stored");
  eq(val(h, "cash", "Free cash flow (₹ cr)"), "N/A", "no stored free cash flow: N/A"); no(un(grp(h, "cash")), /\b100\b/, "never operating minus investing (400 - 300 = 100... or 400 + -300)"); no(un(grp(h, "cash")), /\b700\b/, "nor operating plus investing magnitude");
  // --- valuation
  eq([val(h, "valuation", "P/E"), val(h, "valuation", "P/B"), val(h, "valuation", "EV/EBITDA")], [f2(25.5), f2(12), f2(18)], "valuation from fundamentals");
  // --- placeholders
  eq(un(grp(h, "future")).replace(/^.*?Future Growth Evidence\s*/, ""), "Detailed orders, capex, capacity expansion and management guidance will be added through the News & Announcements research layer.", "future growth evidence: only the placeholder");
  eq(un(grp(h, "ownership")).replace(/^.*?Ownership\s*/, ""), "Shareholding history will be added in the Shareholding Pattern phase.", "ownership: only the placeholder");
  eq(un(grp(h, "developments")).replace(/^.*?Recent Developments\s*/, ""), "Orders, capex, acquisitions and other corporate developments will be added through the News & Announcements layer.", "recent developments: only the placeholder");
  for (const g of ["future", "ownership", "developments"]) { no(un(grp(h, g)), /\d/, g + ": no number at all"); no(grp(h, g), /class="snap-i"/, g + ": no value cell"); }
  no(tx, /promoter|FII|DII|public holding|shareholding\s+[0-9]|\d+(\.\d+)?\s*%\s*(stake|held)/i, "no ownership values anywhere");
  no(tx, /NaN|undefined|null|Infinity|\[object/, "no NaN, undefined, null, Infinity"); no(tx, ADVICE, "no advisory, ranking or score wording");

  // ================= 4. bank: Total income, not Revenue =================
  h = t.S.build("HDFCBANK", d);
  eq(rowNames(h), ["Total income", "Profit before tax", "Profit after tax", "EPS, basic (₹)"], "bank: Total income (not Revenue)"); no(grp(h, "growth"), /<p class="snap-rl">Revenue</, "no Revenue row for a bank");
  r = rowOf(h, "Total income"); eq(r.map((x) => x.l), ["FY2026", "vs previous year", "3-yr CAGR (FY23–FY26)"], "default 3-year window for the bank"); eq(r[0].v, f0(5500), "latest total income"); eq(r[1].v, sp((5500 - 5000) / 5000 * 100), "growth"); eq(r[2].v, sp((Math.pow(5500 / 4000, 1 / 3) - 1) * 100), "3-year CAGR");
  re(un(grp(h, "growth")), /Banks report Total income, not Revenue/, "the bank note");
  eq(val(h, "profitability", "ROCE %"), "-", "missing ROCE is -, not 0"); eq(val(h, "valuation", "EV/EBITDA"), "-", "missing EV/EBITDA is -, not 0");
  eq(val(h, "price", "Latest Price (last close, ₹)"), f2(1455), "bank price"); eq(val(h, "price", "Day change vs previous close"), sp(-3), "negative day change"); eq(cls(h, "price", "Day change vs previous close"), "dn", "styled as a fall");
  for (const p of PER) eq(val(h, "price", p + " return"), "-", p + " return: history too short, so - (never 0)");
  // the business profile is in 'review' state (no source URL): its description must not leak
  no(h, /LEAK/, "a profile whose source is incomplete is not used, so its description and businesses never appear"); re(un(grp(h, "business")), /Business description: Unavailable \(a profile is on file but its source details need review\)/, "and the business says Unavailable");

  // ================= 5. missing data, one thing at a time =================
  const run = (o, sym = "TCS") => { const x = load(); return x.S.build(sym, D(o)); };
  h = run({ prof: null }); re(un(grp(h, "business")), /Business description: Unavailable\./, "no profile file: Unavailable"); no(h, /Provides technology services/, "no description is invented"); eq(val(h, "business", "Company"), "TCS LIMITED", "the company still comes from fundamentals");
  h = run({ prof: { as_of: "x", stocks: [] } }); re(un(grp(h, "business")), /Unavailable\./, "no profile for this stock: Unavailable"); no(grp(h, "business"), /snap-prof/, "no profile block");
  h = run({ prof: { as_of: "x", stocks: [Object.assign({}, PROF().stocks[0], { source_url: "http://insecure.example" })] } }); re(un(grp(h, "business")), /Unavailable/, "a non-https source: Unavailable"); no(h, /Provides technology/, "its text is not shown");
  h = run({ fh: null }); re(un(grp(h, "growth")), /Financial history: Unavailable\./, "financial_history.json missing: growth Unavailable"); no(grp(h, "growth"), /class="snap-r"/, "no growth rows"); eq(val(h, "profitability", "ROE %"), f2(45), "other groups are unaffected");
  h = run({ fh: { schema: 1, stocks: {} } }); re(un(grp(h, "growth")), /Financial history: Unavailable for this stock\./, "stock absent from the history: Unavailable for this stock");
  h = run({ fh: { schema: 1, stocks: [] } }); re(un(grp(h, "growth")), /Financial history: Unavailable\./, "malformed history container");
  h = t.S.build("INFY", d); r = rowOf(h, "Revenue"); eq(r.map((x) => x.l), ["FY2026", "vs previous year", "CAGR"], "INFY: window FY23-FY26 is incomplete, so the CAGR has no window label"); eq([r[0].v, r[1].v, r[2].v], [f0(1000), "-", "-"], "FY2025 revenue missing: change is - and CAGR is -; the latest value is shown");
  r = rowOf(h, "Profit before tax"); eq([r[0].v, r[1].v, r[2].v], [f0(120), "-", "-"], "PBT: FY2025 missing, so no change"); r = rowOf(h, "Profit after tax"); eq([r[0].v, r[1].v], [f0(100), sp(100 / 90 * 100 - 100)], "PAT: both years present, change is shown"); eq(rowOf(h, "EPS, basic (₹)")[0].v, f2(6), "EPS as reported");
  eq(val(h, "price", "Latest Price (last close, ₹)"), "-", "INFY has one valid candle: the comparison rule needs two, so no price is shown"); h = run({ prices: PRICES({ INFY: { candles: [cd("2026-10-02", 1500)] } }) }, "INFY");
  eq([val(h, "price", "Latest Price (last close, ₹)"), val(h, "price", "Day change vs previous close")], ["-", "-"], "INFY (one candle): the comparison rule gives - and -; no value is made up");
  h = t.S.build("ZED", d); eq([rowOf(h, "Revenue")[0].v, rowOf(h, "Revenue")[1].v, rowOf(h, "Profit before tax")[0].v, rowOf(h, "EPS, basic (₹)")[0].v], ["-", "-", "-", "-"], "the latest fiscal year has no value: -, never 0, and no value is carried forward from the year before"); eq(rowOf(h, "Profit after tax")[0].v, f0(45), "a value that is there is shown");
  // valuation: each missing field is -
  h = t.S.build("INFY", d); eq([val(h, "valuation", "P/E"), val(h, "valuation", "P/B"), val(h, "valuation", "EV/EBITDA")], ["-", "-", "-"], "missing valuation fields are -"); eq([val(h, "profitability", "ROE %"), val(h, "profitability", "ROCE %"), val(h, "profitability", "ROA %")], ["-", "-", "-"], "missing ratios are -");
  eq([val(h, "health", "Total assets (₹ cr)"), val(h, "health", "Liabilities / Equity")], ["-", "-"], "missing balance-sheet values are -"); eq(val(h, "health", "Debt / Equity"), "N/A", "debt / equity stays N/A");
  eq([val(h, "cash", "Operating (₹ cr)"), val(h, "cash", "Investing (₹ cr)"), val(h, "cash", "Financing (₹ cr)")], ["-", "-", "-"], "missing cash flow values are -"); eq(val(h, "cash", "Free cash flow (₹ cr)"), "N/A", "FCF N/A");
  h = run({ val: null }); eq([val(h, "valuation", "P/E"), val(h, "profitability", "ROE %")], ["-", "-"], "fundamentals.json missing: -");
  h = run({ fin: null }); eq([val(h, "health", "Total assets (₹ cr)"), val(h, "cash", "Operating (₹ cr)")], ["-", "-"], "financials.json missing: -");
  h = run({ prices: null }); re(un(grp(h, "price")), /Historical price data: Unavailable\./, "historical.json missing: price Unavailable"); eq([val(h, "price", "Latest Price (last close, ₹)"), val(h, "price", "1Y return")], ["-", "-"], "and the values are -");
  // everything missing
  for (const sym of ["XYZ", "TCS"]) { const x = load(); h = x.S.build(sym, { val: null, fin: null, fh: null, prices: null, prof: null }); no(un(h), /NaN|undefined|null|Infinity|\[object/, "nothing available (" + sym + "): no NaN or undefined"); eq(ids(h).length, 10, "all ten groups still render");
    for (const it of items(h)) ok(/^(-|N\/A|Unavailable)$/.test(it.v), "nothing available: every value is -, N/A or Unavailable (" + it.l + ": " + it.v + ")"); }
  { const x = load(); h = x.S.build("XYZ", D()); eq(items(h).every((i) => /^(-|N\/A|Unavailable)$/.test(i.v)), true, "a stock in none of the files: all dashes"); re(un(grp(h, "growth")), /Unavailable for this stock/, "growth says so"); }
  { const x = load(); h = x.S.build("TCS", undefined); eq(ids(h).length, 10, "build(s) with no data object does not throw"); h = x.S.build("TCS", null); eq(ids(h).length, 10, "build(s, null) does not throw"); }
  // a genuine zero is 0.00%, not a dash, and a missing value is never turned into zero
  h = t.S.build("FLAT", d); eq(val(h, "price", "Day change vs previous close"), "0.00%", "no change is shown as 0.00%"); eq(cls(h, "price", "Day change vs previous close"), "", "and is not styled as a rise or fall"); eq(val(h, "price", "1M return"), "0.00%", "flat 1M return is 0.00%"); eq(val(h, "price", "3M return"), "-", "no history that far back: - (not 0.00%)");
  // a stock whose only price rows are bad
  h = run({ prices: PRICES({ TCS: { candles: [{ date: "bad", close: 5 }, { date: "2026-10-02", close: "x" }] } }) }); eq([val(h, "price", "Latest Price (last close, ₹)"), val(h, "price", "Day change vs previous close"), val(h, "price", "5Y return")], ["-", "-", "-"], "invalid candles are ignored: dashes");
  re(un(grp(h, "price")), /No valid daily candles are on file for this stock\./, "and the note says there are none");
  // the other modules are missing
  for (const [drop, label, check] of [["SLResearch", "Stock Research", (x) => [val(x, "valuation", "P/E"), val(x, "business", "Company")]], ["SLFinHistory", "Financial History", (x) => [un(grp(x, "growth")).includes("Unavailable")]], ["SLCompare", "comparison module", (x) => [un(grp(x, "price")).includes("Price calculations are unavailable.")]]]) {
    const x = load({ drop: [drop] }); const o = x.S.build("TCS", d); no(un(o), /NaN|undefined|null\b|Infinity/, "without " + label + " the snapshot still renders"); eq(ids(o).length, 10, "all groups without " + label);
    if (drop === "SLResearch") eq(check(o), ["Unavailable", "Unavailable"], "without Stock Research its values say Unavailable (not a guess)"); else ok(check(o)[0], "without " + label + " the dependent group says Unavailable"); }
  // a stored debt / equity is shown; a stored FCF is shown; neither is derived
  { const f = FIN(); f.stocks[0].balance_sheet.debt_to_equity = 0.5; f.stocks[0].cash_flow.free_cash_flow = 250; const x = load(); h = x.S.build("TCS", D({ fin: f }));
    eq(val(h, "health", "Debt / Equity"), "0.50", "a real stored debt / equity is shown"); eq(val(h, "health", "Liabilities / Equity"), "1.50", "and stays separate from liabilities / equity"); eq(val(h, "cash", "Free cash flow (₹ cr)"), "250", "a stored free cash flow is shown as stored (250, not 400 - 300 = 100)"); }
  { const f = FIN(); f.stocks[0].balance_sheet.debt_to_equity = "0.5"; f.stocks[0].balance_sheet.total_debt = 100; f.stocks[0].cash_flow.free_cash_flow = "250"; const x = load(); h = x.S.build("TCS", D({ fin: f }));
    eq(val(h, "health", "Debt / Equity"), "N/A", "a non-numeric stored debt / equity is N/A (total_debt is not used to build one)"); eq(val(h, "cash", "Free cash flow (₹ cr)"), "N/A", "a non-numeric free cash flow is N/A"); }
  { const f = FIN(); f.stocks[0].cash_flow = { period: "Mar 2025", basis: "consolidated", operating: 400, investing: -300, financing: -50, free_cash_flow: 0 }; const x = load(); h = x.S.build("TCS", D({ fin: f })); eq(val(h, "cash", "Free cash flow (₹ cr)"), "0", "a stored zero free cash flow is a real 0, not N/A"); }
  // text from data is escaped
  { const v = VAL(); v.stocks[0].company_name = "A <b>&\"B\"</b>"; v.stocks[0].sector = "<script>x</script>"; const x = load(); h = x.S.build("TCS", D({ val: v })); no(h, /<script|<\/script/, "company and sector are escaped (no raw tags from data)"); re(h, /A &lt;b&gt;&amp;&quot;B&quot;&lt;\/b&gt;/, "escaped text is intact"); }

  // ================= 6. reuse: the same numbers as the existing code =================
  { const x = load(); const C = window.SLCompare, F = window.SLFinHistory, R = window.SLResearch;
    const pr = C.priceFor(d.prices, "TCS"), pf = C.performance(d.prices, "TCS");
    h = x.S.build("TCS", d); eq(val(h, "price", "Latest Price (last close, ₹)"), f2(pr.price), "price = the comparison page's priceFor"); eq(val(h, "price", "Day change vs previous close"), sp(pr.change), "day change = priceFor");
    for (const p of PER) eq(val(h, "price", p + " return"), sp(pf.ret[p]), p + " = the Phase 5D performance value");
    const m = F.model(d.fh, "TCS"), last = m.cols.length - 1; ["revenue", "profit_before_tax", "profit_after_tax"].forEach((k, i) => { const row = rowOf(h, ["Revenue", "Profit before tax", "Profit after tax"][i]); eq(row[1].v, sp(F.change(m, last, k)), k + " change = F.change"); eq(row[2].v, sp(F.cagr(m, k).pct), k + " CAGR = F.cagr"); eq(row[2].l, F.cagr(m, k).label, k + " CAGR label = F.cagr label"); });
    const secs = R.sections("TCS", { val: d.val, fin: d.fin, hist: d.prices, prof: d.prof });
    const sv = (t2, l) => secs.find((s) => s.title === t2).items.find((i) => i.l === l).v;
    eq([val(h, "profitability", "ROE %"), val(h, "health", "Total assets (₹ cr)"), val(h, "cash", "Operating (₹ cr)"), val(h, "valuation", "P/B")], [sv("Fundamental Quality", "ROE %"), sv("Financial Health", "Total assets"), sv("Cash Flow", "Operating cash flow"), sv("Valuation", "P/B")], "values = the Stock Research items"); }
  // the page's own Financial History panel shows the same growth strings
  { const x = load(); const F = window.SLFinHistory; const panel = un(F.build("TCS", d.fh)); h = x.S.build("TCS", d);
    for (const s of [sp((1500 - 1331) / 1331 * 100), sp(20), sp((Math.pow(1500 / 1000, 1 / 4) - 1) * 100)]) ok(panel.includes(s), "the Financial History panel also shows " + s); ok(un(h).includes(sp((1500 - 1331) / 1331 * 100)), "and so does the snapshot"); }

  // ================= 7. where it appears =================
  // 7a. never on the dashboard or the comparison page, and no fetch there
  for (const hash of ["", "#compare=TCS,INFY", "#compare-pick", "#stock", "#stock=", "#stock=<b>", "#other"]) { const x = load({ hash, files: FILES(d) }); x.box.innerHTML = STATIC_VIEW("TCS"); await sleep(40); eq(x.node().length, 0, "no snapshot at hash '" + hash + "'"); eq(x.fetched.length, 0, "and no fetch at hash '" + hash + "'"); }
  { const x = load({ hash: "#stock=TCS", files: FILES(d) }); await sleep(30); eq(x.node().length, 0, "nothing is placed before the detail view has rendered"); eq(x.fetched.length, 0, "and nothing is fetched"); x.box.innerHTML = '<p class="note">Loading TCS…</p>'; await sleep(30); eq(x.node().length, 0, "the loading message is not enough"); }
  { const x = load({ hash: "#stock=TCS", files: FILES(d) }); x.box.innerHTML = '<p>x</p>' + OV + "<p>no chart container yet</p>"; await sleep(30); eq(x.node().length, 0, "an Overview heading without the finished detail view (no chart container): nothing is placed"); eq(x.fetched.length, 0, "and nothing is fetched"); }
  { const x = load({ hash: "#stock=TCS", files: FILES(d) }); x.box.innerHTML = '<p>x</p><div id="detailChart">c</div>'; await sleep(30); eq(x.node().length, 0, "no Overview heading to anchor on: nothing is placed (never appended at the end)"); }
  // 7b. on Stock Detail: placed once, before Overview, after the price header
  t = load({ hash: "#stock=TCS", files: FILES(d) }); await t.show("TCS", 80);
  eq(t.node().length, 1, "exactly one snapshot on the Stock Detail view"); let page = t.page();
  const iPrice = page.indexOf("price note"), iSnap = page.indexOf('id="detailChart"') >= 0 ? page.indexOf("Investor Snapshot</h3>") : -1, iOv = page.indexOf(OV);
  ok(iPrice >= 0 && iSnap > iPrice && iOv > iSnap, "order: the price header, then the Investor Snapshot, then Overview"); ok(page.indexOf("Financial Health</h3>") > iOv, "Overview and the existing sections follow the snapshot");
  re(t.snap(), /data-snap="price"/, "filled with data after the load"); eq(val(t.snap(), "business", "Company"), "TCS LIMITED", "TCS data");

  // 7c. no duplicate fetch: exactly one request per existing file, none other, and navigation does not repeat them
  eq([...t.fetched].sort(), ["out/company_profiles.json", "out/financial_history.json", "out/financials.json", "out/fundamentals.json", "out/historical.json"], "exactly the five existing files, one request each");
  ok(!t.fetched.some((u) => /scans/.test(u)), "scans.json is not requested");
  await t.show("HDFCBANK", 80); await t.show("TCS", 80); await t.show("INFY", 80); eq(t.fetched.length, 5, "visiting more stocks does not fetch again"); eq(t.node().length, 1, "still one snapshot (the old one is replaced with the page)"); eq(t.styles.filter((s) => s.id === "snapshotStyle").length, 1, "one style element after several placements");
  // 7d. each view shows its own stock
  await t.show("HDFCBANK", 80); eq(val(t.snap(), "business", "Company"), "HDFC BANK LTD", "HDFCBANK view shows HDFCBANK"); eq(rowNames(t.snap())[0], "Total income", "and its bank definition"); no(t.snap(), /TCS LIMITED/, "no TCS data on the HDFCBANK view");
  await t.show("TCS", 80); eq(val(t.snap(), "business", "Company"), "TCS LIMITED", "back to TCS: TCS data"); eq(rowNames(t.snap())[0], "Revenue", "and its non-bank definition");
  // 7e. stale responses never reach the wrong stock
  { const x = load({ hash: "#stock=TCS", files: FILES(d), delay: 40 }); window.location.hash = "#stock=TCS"; x.box.innerHTML = STATIC_VIEW("TCS"); await sleep(10); const first = x.node()[0]; ok(first && /Loading/.test(first.innerHTML), "the first node is waiting for data");
    window.location.hash = "#stock=HDFCBANK"; x.box.innerHTML = STATIC_VIEW("HDFCBANK"); await sleep(10); await sleep(200);
    ok(/Loading/.test(first.innerHTML), "the slow answer for TCS is not written into a node that has been replaced"); eq(x.node().length, 1, "one node"); eq(val(x.snap(), "business", "Company"), "HDFC BANK LTD", "the new node shows HDFCBANK"); no(x.snap(), /TCS LIMITED/, "never TCS data");
    ok(x.fetched.length === 5, "and still only five requests"); }
  { const x = load({ hash: "#stock=TCS", files: FILES(d), delay: 30 }); x.box.innerHTML = STATIC_VIEW("TCS"); await sleep(5); window.location.hash = ""; await sleep(150); ok(/Loading/.test(x.node()[0].innerHTML), "leaving Stock Detail before the data arrives: nothing is written"); }
  // 7f. every file failing
  { const x = load({ hash: "#stock=TCS", files: {} }); await x.show("TCS", 80); eq(x.node().length, 1, "all five files unavailable: the snapshot still appears"); const o = x.snap(); eq(ids(o).length, 10, "with all groups"); no(un(o), /NaN|undefined|null\b|Infinity/, "no NaN"); ok(items(o).every((i) => /^(-|N\/A|Unavailable)$/.test(i.v)), "and every value is a dash, N/A or Unavailable"); }
  { const x = load({ hash: "#stock=TCS", files: Object.assign(FILES(d), { "historical.json": null }) }); await x.show("TCS", 80); eq(val(x.snap(), "profitability", "ROE %"), f2(45), "one failed file does not affect the others"); re(un(x.snap()), /Historical price data: Unavailable/, "price says Unavailable"); }

  // ================= 8. with the real Stock Detail view: parity with its price tiles =================
  { const x = load({ hash: "#stock=TCS", files: FILES(d), full: true }); await sleep(250); const pg = x.page();
    ok(/Stock Detail: TCS/.test(pg), "the real Stock Detail view rendered"); eq(x.node().length, 1, "one snapshot with the real view");
    const tile = (l) => { const m = pg.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + l.replace(/[()₹]/g, "\\$&") + "</span>")); return m ? m[1] : "NOT FOUND"; };
    eq(val(x.snap(), "price", "Latest Price (last close, ₹)"), tile("Latest Price (last close, ₹)"), "the snapshot price equals the Stock Detail price tile"); eq(val(x.snap(), "price", "Day change vs previous close"), tile("Day change vs previous close"), "and the day change equals its tile");
    const iT = pg.indexOf("Latest Price (last close"), iS = pg.indexOf("Investor Snapshot</h3>"), iO = pg.indexOf(">Overview</h3>"); ok(iT >= 0 && iS > iT && iO > iS, "real view: price tiles, then the snapshot, then Overview");
    ok(pg.indexOf("Financial Health <small") > iO || pg.indexOf("Financial Health") > iO, "the existing Overview and Financial Health sections are still there, after it");
    eq(tile("P/E"), val(x.snap(), "valuation", "P/E"), "the snapshot P/E equals the Overview tile"); eq(tile("ROE %"), val(x.snap(), "profitability", "ROE %"), "ROE equals its tile");
    eq(tile("Total assets"), val(x.snap(), "health", "Total assets (₹ cr)"), "total assets equals its tile"); eq(tile("Operating cash flow"), val(x.snap(), "cash", "Operating (₹ cr)"), "operating cash flow equals its tile"); }
  { const x = load({ hash: "#stock=HDFCBANK", files: FILES(d), full: true }); await sleep(250); const pg = x.page(); const tile = (l) => (pg.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + l.replace(/[()₹]/g, "\\$&") + "</span>")) || [])[1];
    eq(val(x.snap(), "price", "Latest Price (last close, ₹)"), tile("Latest Price (last close, ₹)"), "bank: price equals the tile"); eq(val(x.snap(), "price", "Day change vs previous close"), tile("Day change vs previous close"), "bank: day change equals the tile"); }

  // ================= 9. View details =================
  { const x = load({ hash: "#stock=TCS", files: FILES(d) }); await x.show("TCS", 80); const o = x.snap();
    const gos = [...o.matchAll(/data-snap-go="([A-Za-z]+)"/g)].map((m) => m[1]); ok(gos.length >= 6, "most groups have a View details control"); ok(gos.every((g) => ["detailTech", "detailResearch", "detailFinancialHistory"].includes(g)), "each points at an existing section id: " + [...new Set(gos)].join(","));
    no(o, /href="#/, "no #anchor link (it would change the Stock Detail route)"); re(o, /<button type="button" class="lk" data-snap-go="detailTech">View details<\/button>/, "a real button");
    eq(/data-snap="price"[^]*?data-snap-go="detailTech"/.test(o), true, "Price & Performance goes to the technical snapshot"); eq(/data-snap="growth"[^]*?data-snap-go="detailFinancialHistory"/.test(o), true, "Growth goes to Financial History");
    const target = x.els.detailFinancialHistory || (document.getElementById("detailFinancialHistory")); const before = window.location.hash;
    for (const f of x.listeners.click || []) f({ target: { closest: (s) => (s === "[data-snap-go]" ? { getAttribute: () => "detailFinancialHistory" } : null) } });
    eq(document.getElementById("detailFinancialHistory").scrolled, { block: "start" }, "clicking scrolls the existing section into view"); eq(window.location.hash, before, "and does not change the address");
    document.getElementById("detailTech").scrolled = "untouched"; for (const f of x.listeners.click || []) f({ target: { closest: () => null } }); for (const f of x.listeners.click || []) f({ target: null }); eq([document.getElementById("detailTech").scrolled, window.location.hash], ["untouched", before], "other clicks and a null target are ignored"); }

  // ================= 10. wording =================
  for (const sym of ["TCS", "HDFCBANK", "INFY", "FLAT", "XYZ"]) { const x = load(); const o = x.S.build(sym, d); no(un(o), ADVICE, sym + ": no advice, ranking, score, signal, target or valuation-opinion wording"); no(un(o), /\b(cheap|expensive|good|bad|healthy|weak|strong|improving|declining|attractive)\b/i, sym + ": no judgement words"); }
  { const x = load(); const o = un(x.S.build("TCS", d)); re(o, /Descriptive only|Facts already on this page/, "it presents itself as descriptive facts"); }
  // the whole page text of the snapshot code (comments excluded) has no advisory words either
  no(snapCode.replace(/\/\*[\s\S]*?\*\//g, "").replace(/"[^"]*"|'[^']*'/g, (m) => (/[a-z]{4,}\s[a-z]{3,}/i.test(m) ? m : "")), /\b(buy|sell|bullish|bearish|rating|score|winner|ranking)\b/i, "no advisory words in the strings of the code");

  // ================= 11. mobile layout assumptions =================
  const css = (snapCode.match(/var STYLE='([^]*?)';\nfunction addStyle/) || [])[1] || ""; ok(css.length > 300, "the style rules are found");
  re(css, /#detailSnapshot \.snap\{display:grid;grid-template-columns:repeat\(auto-fit,minmax\(min\(\d+px,100%\),1fr\)\)/, "groups: an auto-fit grid whose minimum is capped by the container (min(…,100%)), so one column on a phone");
  re(css, /\.snap-kv\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(min\(\d+px,100%\),1fr\)\)/, "values: an auto-fill grid with a capped minimum");
  for (const m of css.matchAll(/minmax\(([^)]*\)[^)]*)\)/g)) ok(/^min\(\d+px,100%\),1fr$/.test(m[1]), "every minmax is capped by 100%: " + m[0]);
  for (const m of css.matchAll(/min\((\d+)px,100%\)/g)) ok(+m[1] <= 320, "no minimum wider than a 320px screen: " + m[0]);
  no(css.replace(/#detailSnapshot \.snap-h \.lk\{[^}]*\}/, ""), /(^|[^-])(min-)?width:\s*\d+px|white-space:\s*nowrap/, "no fixed pixel width, no unwrapped text (apart from the View details button)"); re(css, /\.snap-i b\{[^}]*overflow-wrap:anywhere/, "long values wrap instead of widening the page"); re(css, /\.snap-g\{[^}]*min-width:0/, "grid children may shrink"); no(css, /overflow-x|overflow:\s*(auto|scroll)/, "no scrolling box inside the snapshot");
  re(css, /\.snap-g\.wide\{grid-column:1\/-1\}/, "wide groups span the row on desktop"); no(css, /@media[^{]*min-width/, "no desktop-only layout; the one rule set works at every width");
  no(css, /rgba?\(|#[0-9a-fA-F]{3,6}\b/, "colours come from the page's own variables, so light and dark both work");
  { const x = load(); const o = x.S.build("TCS", d); no(o, /style="[^"]*(width|min-width)\s*:\s*\d{3,}px/, "no wide inline sizes in the output"); ok(Math.max(...[...o.matchAll(/class="snap-i"><span>(.*?)<\/span>/g)].map((m) => un(m[1]).length)) <= 40, "labels are short enough to wrap in a narrow cell"); }
  { const x = load(); const o = x.S.build("TCS", d); eq((o.match(/<section|<aside/g) || []).length, 0, "no nested sections"); eq((o.match(/id="/g) || []).length, 0, "the output adds no element ids that could collide with the page"); }

  console.log("Investor snapshot tests passed (" + checks + " checks)");
})().catch((x) => { console.error("INVESTOR SNAPSHOT TEST FAILED:", x.message, x.stack ? "\n" + x.stack.split("\n").slice(1, 4).join("\n") : ""); process.exit(1); });
