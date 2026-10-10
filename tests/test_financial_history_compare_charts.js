// Run: node tests/test_financial_history_compare_charts.js   (no network, no browser)
// Tests the Phase 5C comparison charts: valuation, profitability, latest financial and growth charts for 2-5 stocks, drawn as inline SVG from the same data and the same
// functions as the comparison tables. Covers bar geometry (zero-based, negatives below zero, a missing value is an empty slot and never a zero), the unavailable states, banks
// vs other companies, one fiscal year per chart, fixed-window CAGR, 2- and 5-stock layouts, responsive markup, neutral wording, and that the tables and the rest of the page
// are exactly what they were at Phase 5B.1 (700767f).
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
// Phase 5I: behaviour is tested on the page as shipped (html). The byte-identity pins below are tested on PINHTML = the page minus the Phase 5I layer (legacy_5i.js, proven exact against aa4ace1 by test_global_navigation.js), because Phase 5I deliberately changes the route readers and the search mount.
const PINHTML = require("./legacy_5i.js").legacy(html), PINBLOCKS = PINHTML.split("<script>").slice(1).map((b) => b.split("</script>")[0]), PINDETAIL = PINBLOCKS.find((b) => b.includes("Stock Detail view"));
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const fhCode = modOf(html, FHTAG), cmpCode = modOf(html, CMPTAG);
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const near = (a, b, tol, m) => { checks++; assert.ok(Math.abs(a - b) <= tol, m + " (got " + a + ", expected " + b + ")"); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
const load = (code) => { global.MutationObserver = undefined; global.window = { location: { hash: "" }, addEventListener() {}, scrollTo() {} }; global.document = { getElementById: () => null, querySelector: () => null, body: { classList: { add() {}, remove() {} } } }; global.fetch = async () => ({ ok: false }); (0, eval)(code); };
load(fhCode); const F = window.SLFinHistory; load(cmpCode); const C = window.SLCompare; window.SLFinHistory = F;
ok(F && C && typeof C.build === "function", "modules load");

// ---------- fixtures (tests only) ----------
const V = (o) => Object.assign({ revenue: null, total_revenue: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, operating_cash_flow: null }, o);
const R = (fy, values, official = false) => ({ fy, period: "Mar " + fy, basis: "consolidated", values: V(values), verification: { status: "verified", reconciliation: "matched" }, source: { provider: official ? "Official annual report" : "Upstox" } });
const series = (bank, fy0, n, start, step = 1.1, o = {}) => Array.from({ length: n }, (_, i) => { const v = +(start * Math.pow(step, i)).toFixed(4); return R(fy0 + i, Object.assign({ [bank ? "total_revenue" : "revenue"]: v, profit_before_tax: v / 2, profit_after_tax: v / 4, eps_basic: 10 + i, operating_cash_flow: v / 3 }, o)); });
const STK = (sym, bank, years) => ({ symbol: sym, statement_layout: bank ? "financial" : "standard", years });
const HDOC = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox", stocks: {
  TCS: STK("TCS", false, [R(2022, { revenue: 100, profit_before_tax: 50, profit_after_tax: 25, eps_basic: 10, operating_cash_flow: 33 }, true), ...series(false, 2023, 4, 110)]),
  INFY: STK("INFY", false, series(false, 2023, 4, 200)), RELIANCE: STK("RELIANCE", false, series(false, 2023, 4, 900)), LT: STK("LT", false, series(false, 2023, 4, 300)),
  BHARTIARTL: STK("BHARTIARTL", false, series(false, 2023, 4, 150, 1.2)),
  HDFCBANK: STK("HDFCBANK", true, series(true, 2023, 4, 1000, 1.1, { operating_cash_flow: -80 })), SBIN: STK("SBIN", true, series(true, 2023, 4, 600, 1.05)), ICICIBANK: STK("ICICIBANK", true, series(true, 2023, 4, 700)),
  EARLY: STK("EARLY", false, series(false, 2022, 4, 50)),                                                   // latest year FY2025
  NEGSTART: STK("NEGSTART", false, [R(2023, { revenue: -5, profit_after_tax: -50 }), R(2024, { revenue: 10 }), R(2025, { revenue: 20 }), R(2026, { revenue: 40, profit_before_tax: 0 })]),
  FLAT: STK("FLAT", false, [R(2023, { revenue: 100 }), R(2024, { revenue: 100 }), R(2025, { revenue: 100 }), R(2026, { revenue: 100, profit_after_tax: 5 })]),
  GAPPY: STK("GAPPY", false, [R(2023, { revenue: 10 }), R(2024, { revenue: 11 }), R(2026, { revenue: 14 })]) } });
const RAT = { TCS: [21, 3.1, 11, 16, 19, 6], INFY: [22, 3.2, 12, 17, 20, 7], RELIANCE: [30, 2.5, 9, 9, 10, 4], LT: [35, 5, 14, 14, 15, 5], BHARTIARTL: [40, 8, 15, 20, 22, 3], HDFCBANK: [18, 2.8, null, 16, null, 2], SBIN: [9, 1.1, null, 14, null, 1], ICICIBANK: [17, 2.9, null, 17, null, 2], EARLY: [12, 1, 5, 8, 9, 3], NEGSTART: [-5, 0.5, 4, -2, 1, 0], FLAT: [10, 1.5, 6, 5, 6, 4], GAPPY: [null, null, null, null, null, null] };
const FUND = () => ({ as_of: "2026-10-01", source: "Upstox", stocks: Object.keys(RAT).map((s) => { const r = RAT[s]; return { symbol: s, company_name: s + " Ltd", sector: "X", pe: r[0], pb: r[1], ev_ebitda: r[2], roe: r[3], roce: r[4], roa: r[5] }; }) });
const cd = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const PRICES = () => ({ stocks: { TCS: { candles: [cd("2026-10-01", 100), cd("2026-10-02", 101)] } } });
const D = (o = {}) => Object.assign({ val: FUND(), hist: HDOC(), prices: PRICES() }, o);
const build = (syms, d = D()) => C.build("#compare=" + syms.join(","), d);

// ---------- helpers that read the chart markup ----------
const fig = (h, key) => { const m = h.match(new RegExp('<figure data-cchart="' + key + '"[^>]*>.*?</figure>', "s")); return m ? m[0] : null; };
const keys = (h, grp) => { const g = h.match(new RegExp('<div class="panel"[^>]*data-cgroup="' + grp + '">.*?(?=<div class="panel"[^>]*data-cgroup=|<h3 style="margin:1.6em 0 .2em">Financial history)', "s")); return g ? [...g[0].matchAll(/<figure data-cchart="([^"]+)"/g)].map((x) => x[1]) : null; };
const groups = (h) => [...h.matchAll(/data-cgroup="([a-z]+)"/g)].map((x) => x[1]);
const num = (s, a) => parseFloat(s.match(new RegExp(" " + a + '="([-0-9.]+)"'))[1]);
const bars = (f) => [...f.matchAll(/<rect data-sym="([^"]+)"([^>]*)>/g)].map((m) => ({ sym: m[1], x: num(m[0], "x"), y: num(m[0], "y"), w: num(m[0], "width"), h: num(m[0], "height") }));
const zeroY = (f) => num(f.match(/<line[^>]*data-zero="1"[^>]*>/)[0], "y1");
const labels = (f) => [...f.matchAll(/<text data-label="([^"]+)"[^>]*>(.*?)<\/text>/g)].map((m) => [m[1], text(m[2])]);
const names = (f) => [...f.matchAll(/<text data-name="([^"]+)" x="([-0-9.]+)" y="([-0-9.]+)"/g)].map((m) => ({ sym: m[1], x: +m[2], y: +m[3] }));
const aria = (f) => (f.match(/aria-label="([^"]*)"/) || [])[1];
const title = (f) => text(f.match(/<figcaption><b>(.*?)<\/b>/)[1]);
const lblMap = (f) => Object.fromEntries(labels(f));
const pcs = (v) => (v === null ? "-" : (v > 0 ? "+" : "") + v.toFixed(2) + "%");

// ================= 1. structure, order, placement =================
let h = build(["TCS", "INFY"]);
eq(groups(h), ["valuation", "profitability", "latest", "growth"], "four groups, in this order");
eq(keys(h, "valuation"), ["pe", "pb", "ev_ebitda"], "valuation: P/E, P/B, EV/EBITDA"); eq(keys(h, "profitability"), ["roe", "roce", "roa"], "profitability: ROE, ROCE, ROA");
eq(keys(h, "latest"), ["level-revenue", "level-profit_before_tax", "level-profit_after_tax", "level-operating_cash_flow"], "latest: revenue, PBT, PAT, operating cash flow");
eq(keys(h, "growth"), ["change-revenue", "change-profit_before_tax", "change-profit_after_tax", "cagr-revenue", "cagr-profit_before_tax", "cagr-profit_after_tax"], "growth: change and fixed-window CAGR for revenue, PBT, PAT");
re(h, /data-ccharts-title="1">Charts<\/h3>/, "one Charts heading"); eq((h.match(/data-ccharts-title/g) || []).length, 1, "only once");
ok(h.indexOf('data-ctable="overview"') < h.indexOf("data-ccharts-title") && h.indexOf("data-ccharts-title") < h.indexOf('data-ctable="revenue"'), "charts sit after the overview table and before the history tables");
ok(h.indexOf("data-compare=") < h.indexOf("data-ccharts-title") && h.indexOf("data-ccharts-title") < h.indexOf("Side-by-side data only"), "inside the comparison block, above the closing disclaimer");
eq((h.match(/<figure data-cchart=/g) || []).length, 16, "sixteen charts for a two-stock non-bank comparison");
eq((h.match(/<svg /g) || []).length, 16, "all sixteen are drawn (every one has two values)");
no(h, /<canvas|<img|createChart|LightweightCharts/, "no canvas, no image, no chart library");
for (const hash of ["#compare=TCS", "#compare-pick=TCS", "#compare-pick", "#compare=", "#stock=TCS"]) no(C.build(hash, D()), /data-cchart|data-ccharts-title/, hash + ": no charts without a valid 2-5 comparison");
no(C.build("#compare=TCS,ITC", { val: null, hist: null, prices: null }), /data-cchart/, "no data at all: no charts");

// ================= 2. the tables did not change (Phase 5B.1 output, with the chart section removed) =================
let oldCmp = ""; try { const oldHtml = cp.execSync("git show 700767f:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); oldCmp = modOf(oldHtml, CMPTAG); } catch (e) { oldCmp = ""; }
const stripCharts = (x) => x.replace(/<h3 style="margin:1\.6em 0 \.2em" data-ccharts-title="1">[\s\S]*?(?=<h3 style="margin:1\.6em 0 \.2em">Financial history<\/h3>)/, "");
if (oldCmp) {
  load(oldCmp); window.SLFinHistory = F; const O = window.SLCompare; const old = (hash, d) => O.build(hash, d); load(cmpCode); window.SLFinHistory = F;
  for (const hash of ["#compare=TCS,INFY", "#compare=INFY,TCS", "#compare=HDFCBANK,TCS,SBIN", "#compare=HDFCBANK,SBIN", "#compare=TCS,INFY,RELIANCE,LT,BHARTIARTL", "#compare=TCS,INFY,RELIANCE,LT,BHARTIARTL,SBIN,NOPE", "#compare=GAPPY,EARLY,NEGSTART,FLAT", "#compare=TCS", "#compare-pick=TCS", "#compare-pick", "#compare=NOPE,<b>", "#stock=TCS"]) {
    eq(stripCharts(C.build(hash, D())), old(hash, D()), hash + ": with the charts removed the page is identical to Phase 5B.1 (tables, notices, picker untouched)"); }
  for (const d of [{ val: FUND(), hist: null, prices: null }, { val: null, hist: HDOC(), prices: null }, { val: null, hist: null, prices: null }]) eq(stripCharts(C.build("#compare=TCS,INFY", d)), old("#compare=TCS,INFY", d), "unavailable data: identical too");
}

// ================= 3. bar geometry =================
let f = fig(h, "pe"), y0 = zeroY(f), b = bars(f);
eq(b.map((x) => x.sym), ["TCS", "INFY"], "one bar per stock, in the order chosen"); eq(labels(f), [["TCS", "21.00"], ["INFY", "22.00"]], "labels are the table's strings");
for (const x of b) near(x.y + x.h, y0, 0.11, x.sym + ": a positive bar stands on the zero line");
near(b[0].h / b[1].h, 21 / 22, 0.02, "bar heights are proportional to the values (zero-based axis)");
ok(b[0].x < b[1].x, "bars follow the order chosen, left to right"); near(b[0].w, b[1].w, 0.11, "equal widths"); ok(b[0].w <= 48.1, "two bars are not stretched across the whole chart");
f = fig(build(["TCS", "INFY"]), "level-revenue"); b = bars(f);
near(b[0].h / b[1].h, 146.41 / 266.2, 0.02, "revenue heights follow 146.41 : 266.2");
eq(title(f), "Revenue, FY2026 (₹ crore)", "the title names the fiscal year and the unit"); eq(lblMap(f), { TCS: "146.41", INFY: "266.2" }, "revenue labels");
// negative values hang below the zero line, with their label below the bar
{ const d = D(); d.val.stocks.forEach((s) => { if (s.symbol === "TCS") s.pe = -10; if (s.symbol === "INFY") s.pe = 20; });
  const g = fig(build(["TCS", "INFY"], d), "pe"), z = zeroY(g), bb = bars(g); near(bb[0].y, z, 0.11, "a negative bar starts at the zero line"); ok(bb[0].h > 0, "and hangs below it"); near(bb[0].h / bb[1].h, 0.5, 0.03, "with the right size");
  const lab = [...g.matchAll(/<text data-label="TCS" x="[-0-9.]+" y="([-0-9.]+)"/g)][0]; ok(+lab[1] > z, "the negative label sits below the zero line"); re(g, /-10\.00/, "and shows its sign");
  ok(/>-10<|>-5<|>-15<|>-20</.test(g) || /text-anchor="end"[^>]*>-/.test(g), "the axis reaches below zero"); }
// a genuine zero is drawn as a zero-height bar with its label, not skipped
{ const g = fig(build(["FLAT", "TCS"]), "change-revenue"), bb = bars(g); eq(bb.map((x) => x.sym), ["FLAT", "TCS"], "a flat stock still has a bar"); near(bb[0].h, 0, 0.001, "a 0.00% change is a zero-height bar"); eq(lblMap(g).FLAT, "0.00%", "labelled 0.00%"); }

// ================= 4. missing values stay missing =================
{ const g = fig(build(["TCS", "HDFCBANK", "INFY"]), "ev_ebitda"), bb = bars(g); eq(bb.map((x) => x.sym), ["TCS", "INFY"], "a stock with no EV/EBITDA has no bar"); eq(lblMap(g), { TCS: "11.00", HDFCBANK: "-", INFY: "12.00" }, "its slot is labelled with a dash, not 0");
  eq(names(g).map((x) => x.sym), ["TCS", "HDFCBANK", "INFY"], "but the stock keeps its place and name"); no(aria(g) || "", /HDFCBANK 0|HDFCBANK 0\.00/, "never described as zero"); re(aria(g), /HDFCBANK not available/, "described as not available");
  ok(bb[1].x - bb[0].x > 1.5 * bb[0].w, "the empty slot leaves a gap between the two bars"); }
{ const g = fig(build(["TCS", "HDFCBANK"]), "ev_ebitda"); re(g, /data-state="unavailable"/, "one value out of two: unavailable"); re(text(g), /Not enough values on file to chart this \(needs at least two stocks with a value\)\./, "with the plain message"); no(g, /<svg|<rect/, "and no bars at all"); re(g, /EV\/EBITDA/, "still titled"); }
{ const g = fig(build(["HDFCBANK", "SBIN"]), "roce"); re(g, /data-state="unavailable"/, "no values: unavailable"); no(g, /<rect/, "no bars"); }
{ const g = fig(build(["GAPPY", "TCS"]), "pe"); re(g, /data-state="unavailable"/, "a stock with no fundamentals at all"); }
{ const d = D(); d.val = null; const hh = build(["TCS", "INFY"], d); eq((hh.match(/data-state="unavailable"/g) || []).length, 6, "no fundamentals file: the six ratio charts are unavailable"); ok(!!fig(hh, "level-revenue") && /<svg/.test(fig(hh, "level-revenue")), "and the financial charts still draw"); }
{ const d = D(); d.hist = null; const hh = build(["TCS", "INFY"], d); ok(/<svg/.test(fig(hh, "pe")), "no history file: the ratio charts still draw"); eq(keys(hh, "latest"), [], "no financial charts"); eq(keys(hh, "growth"), [], "and no growth charts"); eq((hh.match(/Financial history unavailable for the selected stocks\./g) || []).length, 2, "each of the two groups says so once"); }
{ const d = D(); d.hist = { stocks: [] }; const hh = build(["TCS", "INFY"], d); eq(keys(hh, "latest"), [], "a malformed history file: no financial charts"); re(hh, /Financial history unavailable for the selected stocks\./, "and a message"); }
{ const g = fig(build(["GAPPY", "TCS", "INFY"]), "level-revenue"); eq(lblMap(g), { GAPPY: "14", TCS: "146.41", INFY: "266.2" }, "GAPPY has FY2026 so it is charted"); }
{ const g = fig(build(["EARLY", "TCS", "INFY"]), "level-revenue"); eq(title(g), "Revenue, FY2026 (₹ crore)", "the chart year is the latest on file among the charted stocks"); eq(lblMap(g), { EARLY: "-", TCS: "146.41", INFY: "266.2" }, "a stock whose latest year is FY2025 is left empty for FY2026, its FY2025 value is NOT carried forward"); eq(bars(g).map((x) => x.sym), ["TCS", "INFY"], "no bar for it"); }
{ const g = fig(build(["EARLY", "GAPPY"]), "level-revenue"); eq(title(g), "Revenue, FY2026 (₹ crore)", "year follows the group"); re(g, /data-state="unavailable"/, "one value only: unavailable"); }
{ const g = fig(build(["GAPPY", "TCS"]), "level-profit_before_tax"); re(g, /data-state="unavailable"/, "GAPPY has no profit before tax at all: one value only"); }
{ const g = fig(build(["NEGSTART", "TCS", "INFY"]), "level-profit_before_tax"); eq(lblMap(g).NEGSTART, "0", "a stored 0 is drawn as 0 (not confused with missing)"); eq(bars(g).length, 3, "three bars"); }

// ================= 5. growth and CAGR use the same figures as the tables =================
{ const hh = build(["TCS", "INFY", "NEGSTART", "FLAT"]), d = D(); const m = (s) => F.model(d.hist, s);
  const g = fig(hh, "change-revenue"); eq(title(g), "Revenue change, FY2026 vs FY2025 (%)", "change title names both years"); const L = lblMap(g);
  for (const s of ["TCS", "INFY", "NEGSTART", "FLAT"]) { const mm = m(s), v = F.change(mm, mm.cols.length - 1, "revenue"); eq(L[s], pcs(v), s + ": the bar's label is the Financial History change figure"); }
  eq(L.NEGSTART, "+100.00%", "NEGSTART 20 -> 40"); eq(L.FLAT, "0.00%", "flat");
  const c = fig(hh, "cagr-revenue"); eq(title(c), "Revenue, CAGR over a fixed window (%)", "CAGR title"); const CL = lblMap(c);
  eq(CL.TCS, "+8.63%".replace("8.63", F.cagr(m("TCS"), "revenue").pct.toFixed(2)), "TCS uses the shared fixed window"); eq(CL.INFY, pcs(F.cagr(m("INFY"), "revenue").pct), "INFY too"); eq(CL.NEGSTART, "-", "a non-positive first year: no CAGR, an empty slot, not zero"); eq(CL.FLAT, "0.00%", "flat CAGR is 0.00%");
  re(text(c), /Windows: TCS FY22–FY26, INFY FY23–FY26, FLAT FY23–FY26\./, "the windows are listed under the chart"); no(text(c), /NEGSTART FY/, "a stock with no CAGR has no window listed"); eq(bars(c).length, 3, "three bars, one empty slot"); }
{ const g = fig(build(["GAPPY", "TCS", "INFY"]), "change-revenue"); eq(lblMap(g).GAPPY, "-", "GAPPY FY2026 follows a missing FY2025: no change, an empty slot"); const c = fig(build(["GAPPY", "TCS", "INFY"]), "cagr-revenue"); eq(lblMap(c).GAPPY, "-", "and no CAGR"); }
{ const g = fig(build(["EARLY", "TCS", "INFY"]), "change-revenue"); eq(title(g), "Revenue change, FY2026 vs FY2025 (%)", "reference year FY2026"); eq(lblMap(g).EARLY, "-", "EARLY has no FY2026: empty (not its FY2025 change)"); }
for (const k of ["change-operating_cash_flow", "cagr-operating_cash_flow", "change-eps_basic", "level-eps_basic"]) eq(fig(build(["TCS", "INFY"]), k), null, k + " does not exist: cash flow gets no growth chart, EPS is not charted");
eq(keys(build(["TCS", "INFY"]), "growth").length, 6, "six growth charts");
{ const hh = build(["TCS", "INFY"]); re(text(hh), /Change is the stock's fiscal year against the year just before it, when both have a value\. CAGR uses a fixed window per stock \(TCS FY2022–FY2026, all others FY2023–FY2026\)/, "the growth group states the rules"); }
{ // ITC boundary stays respected through the shared functions: a FY2023 reference year must not span FY2022
  const d = D(); d.hist.stocks.ITC = STK("ITC", false, [R(2022, { revenue: 50 }, true), R(2023, { revenue: 100 })]); d.hist.stocks.XX = STK("XX", false, [R(2022, { revenue: 10 }), R(2023, { revenue: 12 })]); d.hist.stocks.YY = STK("YY", false, [R(2022, { revenue: 20 }), R(2023, { revenue: 21 })]); d.val.stocks.push({ symbol: "XX" }, { symbol: "ITC" }, { symbol: "YY" });
  const g = fig(build(["ITC", "XX", "YY"], d), "change-revenue"); eq(title(g), "Revenue change, FY2023 vs FY2022 (%)", "reference year FY2023"); eq(lblMap(g), { ITC: "-", XX: "+20.00%", YY: "+5.00%" }, "ITC FY2022 -> FY2023 is not charted as a change, normal stocks are"); }

// ================= 6. banks and non-banks =================
h = build(["HDFCBANK", "TCS", "SBIN", "INFY"]);
eq(keys(h, "latest"), ["level-revenue", "level-total_revenue", "level-profit_before_tax", "level-profit_after_tax", "level-operating_cash_flow"], "a mixed set: separate revenue and total income charts");
eq(title(fig(h, "level-revenue")), "Revenue (non-bank companies), FY2026 (₹ crore)", "non-bank title"); eq(title(fig(h, "level-total_revenue")), "Total income (banks), FY2026 (₹ crore)", "bank title");
eq(bars(fig(h, "level-revenue")).map((x) => x.sym), ["TCS", "INFY"], "only non-banks in the revenue chart"); eq(names(fig(h, "level-revenue")).map((x) => x.sym), ["TCS", "INFY"], "and no slot at all for a bank in it, not even an empty one"); eq(names(fig(h, "level-total_revenue")).map((x) => x.sym), ["HDFCBANK", "SBIN"], "nor a slot for a non-bank in the total income chart"); eq(bars(fig(h, "level-total_revenue")).map((x) => x.sym), ["HDFCBANK", "SBIN"], "only banks in the total income chart");
eq(bars(fig(h, "level-profit_after_tax")).map((x) => x.sym), ["HDFCBANK", "TCS", "SBIN", "INFY"], "profit after tax charts every stock, in the order chosen");
eq(keys(h, "growth"), ["change-revenue", "change-total_revenue", "change-profit_before_tax", "change-profit_after_tax", "cagr-revenue", "cagr-total_revenue", "cagr-profit_before_tax", "cagr-profit_after_tax"], "growth charts split the same way");
eq(title(fig(h, "change-total_revenue")), "Total income (banks) change, FY2026 vs FY2025 (%)", "bank growth title"); eq(title(fig(h, "cagr-revenue")), "Revenue (non-bank companies), CAGR over a fixed window (%)", "non-bank CAGR title");
re(text(h), /Banks report total income, which is not the same measure as revenue, so the two are charted separately and are not compared\./, "the mixed note"); re(text(h), /Bank operating cash flow includes deposit and lending movements and is not comparable with other companies\./, "and the bank cash-flow note");
{ const g = fig(h, "level-operating_cash_flow"), bb = bars(g), z = zeroY(g); const hd = bb.find((x) => x.sym === "HDFCBANK"); near(hd.y, z, 0.11, "a bank's negative operating cash flow hangs below zero"); ok(lblMap(g).HDFCBANK.startsWith("-"), "and shows its sign"); }
{ const g = fig(h, "level-total_revenue"); no(text(g), /Revenue/i, "the bank chart never says Revenue"); }
h = build(["HDFCBANK", "SBIN"]); eq(keys(h, "latest"), ["level-total_revenue", "level-profit_before_tax", "level-profit_after_tax", "level-operating_cash_flow"], "all banks: Total income only"); eq(title(fig(h, "level-total_revenue")), "Total income, FY2026 (₹ crore)", "plain title");
no(text(h.slice(h.indexOf("data-compare="))), /Revenue/i, "the word Revenue is never used anywhere in an all-bank comparison, charts included"); re(text(h), /Bank operating cash flow includes/, "bank cash-flow note"); no(text(h), /not the same measure as revenue/, "and no mixed note");
eq(keys(h, "growth"), ["change-total_revenue", "change-profit_before_tax", "change-profit_after_tax", "cagr-total_revenue", "cagr-profit_before_tax", "cagr-profit_after_tax"], "all banks growth");
h = build(["TCS", "INFY"]); no(text(h), /Total income/, "no bank wording for two non-banks"); no(text(h), /Bank operating cash flow/, "and no bank note");
h = build(["HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY"]); eq(bars(fig(h, "level-total_revenue")).length, 3, "three banks charted together"); eq(bars(fig(h, "level-revenue")).length, 2, "two non-banks charted together");
{ const d = D(); d.hist.stocks.SBIN.years = []; const hh = build(["SBIN", "HDFCBANK", "TCS"], d); eq(bars(fig(hh, "level-profit_after_tax")).map((x) => x.sym), ["HDFCBANK", "TCS"], "a stock with no history is left out of the history charts, like the tables"); eq(bars(fig(hh, "pe")).length, 3, "but is in the ratio charts"); }

// ================= 7. two and five stocks =================
h = build(["TCS", "INFY"]); for (const k of ["pe", "level-revenue", "change-revenue", "cagr-revenue"]) { const g = fig(h, k); eq(names(g).length, 2, k + ": two names"); eq(bars(g).length, 2, k + ": two bars"); }
h = build(["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL"]);
for (const k of ["pe", "pb", "roe", "level-revenue", "level-profit_after_tax", "change-revenue", "cagr-revenue"]) { const g = fig(h, k); eq(names(g).map((x) => x.sym), ["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL"], k + ": five stocks in the order chosen"); eq(bars(g).length, 5, k + ": five bars"); const bb = bars(g); for (let i = 1; i < 5; i++) ok(bb[i].x > bb[i - 1].x + bb[i - 1].w, k + ": bars do not overlap"); for (const x of bb) ok(x.x >= 0 && x.x + x.w <= 320, k + ": every bar is inside the chart width"); }
{ const g = fig(h, "pe"), nm = names(g); ok(new Set(nm.map((x) => x.y)).size === 2, "a long name (BHARTIARTL) in a five-stock chart: names alternate between two rows so none overlap"); const ys = nm.map((x) => x.y); ok(ys.every((y, i) => y === ys[i % 2]), "the alternation is regular");
  const g2 = fig(build(["TCS", "INFY"]), "pe"); eq(new Set(names(g2).map((x) => x.y)).size, 1, "two stocks: one row of names"); }
{ // long VALUE labels (not long names) also alternate between two heights, so they cannot run into each other
  const big = D(); Object.values(big.hist.stocks).forEach((st) => st.years.forEach((y) => { for (const k of ["revenue", "total_revenue"]) if (y.values[k] !== null) y.values[k] *= 100000; }));
  const lift = (g) => { const bb = bars(g), lb = [...g.matchAll(/<text data-label="([^"]+)" x="[-0-9.]+" y="([-0-9.]+)"/g)].map((m) => [m[1], +m[2]]); return bb.map((x) => Math.round(x.y - lb.find((l) => l[0] === x.sym)[1])); };
  const g5 = fig(build(["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL"], big), "level-revenue"); eq([...new Set(lift(g5))].sort((a, b) => a - b), [3, 14], "five stocks with long figures: labels alternate between two heights"); ok(lblMap(g5).INFY.length >= 10, "the figures really are long");
  const g2 = fig(build(["TCS", "INFY"], big), "level-revenue"); eq([...new Set(lift(g2))], [3], "two stocks: every label at one height"); }
eq((h.match(/<figure data-cchart=/g) || []).length, 16, "sixteen charts for five non-banks"); eq((h.match(/<svg /g) || []).length, 16, "all drawn");
eq(Object.keys(build(["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL", "HDFCBANK", "SBIN"]).match(/data-cchart="pe"/g) || []).length, 1, "a request for seven still charts only the first five"); eq(bars(fig(build(["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL", "HDFCBANK", "SBIN"]), "pe")).length, 5, "five bars");
eq(bars(fig(build(["SBIN", "INFY", "TCS"]), "pe")).map((x) => x.sym), ["SBIN", "INFY", "TCS"], "the order of the request, not sorted by value");
ok(JSON.stringify(build(["TCS", "INFY"])) !== JSON.stringify(build(["INFY", "TCS"])), "so reversing the request reverses the charts");
eq(bars(fig(build(["INFY", "TCS"]), "pe")).map((x) => x.sym), ["INFY", "TCS"], "reversed");
{ const hh = build(["LT", "TCS"]); const bb = bars(fig(hh, "pe")); ok(bb[0].h > bb[1].h, "LT (35) is taller than TCS (21) only because it is larger: order was LT first"); const hh2 = build(["TCS", "LT"]); const b2 = bars(fig(hh2, "pe")); ok(b2[0].h < b2[1].h, "swap the request and the order swaps: position is never decided by size"); }

// ================= 8. markup: responsive, accessible, one colour =================
h = build(["TCS", "INFY", "HDFCBANK"]);
for (const g of [...h.matchAll(/<div class="panel"[^>]*data-cgroup="[a-z]+">/g)]) re(g[0], /padding:16px/, "group panels use the page's panel style");
eq((h.match(/grid-template-columns:repeat\(auto-fit,minmax\(min\(280px,100%\),1fr\)\)/g) || []).length, 4, "every group is a responsive grid: two or more columns on a desktop, one on a phone, and never wider than its panel even on a 320px screen (min(280px,100%))"); no(h, /minmax\(280px,1fr\)/, "no fixed 280px minimum that would overflow a 320px screen");
for (const s of h.match(/<svg [^>]*>/g)) { re(s, /viewBox="0 0 320 196"/, "viewBox"); re(s, /role="img"/, "role img"); re(s, /aria-label="[^"]+"/, "aria-label"); re(s, /style="display:block;width:100%;height:auto"/, "scales with its container"); no(s, / (width|height)="/, "no fixed pixel size on the svg"); }
for (const s of h.match(/<figure [^>]*data-state="unavailable"[^>]*>/g) || []) re(s, /margin:0/, "unavailable figure");
const fills = new Set([...h.matchAll(/<rect [^>]*style="fill:([^"]+)"/g)].map((m) => m[1])); eq([...fills], ["var(--ac)"], "every bar uses the same theme colour, so no value is shown as good or bad");
no(h, /fill="(red|green|#[0-9a-fA-F]{3,6}|rgb)/, "no hard-coded colours"); no(h, /class="[^"]*\b(up|dn)\b/, "no good/bad classes"); no(h.match(/<svg[\s\S]*?<\/svg>/g).join(""), /stroke:(red|green)|fill:(red|green)/, "no red or green");
re(h, /style="fill:var\(--ink\)"/, "labels use the theme text colour"); re(h, /style="stroke:var\(--mute\)" stroke-width="1\.2"/, "zero line");
for (const g of h.match(/<svg[\s\S]*?<\/svg>/g)) { const rects = (g.match(/<rect /g) || []).length, titles = (g.match(/<title>/g) || []).length; eq(titles, rects, "every bar has a tooltip title"); }
{ const g = fig(h, "pe"); re(aria(g), /^P\/E\. TCS 21\.00; INFY 22\.00; HDFCBANK 18\.00\.$/, "the aria-label reads the title and every value"); const g2 = fig(h, "ev_ebitda"); re(aria(g2), /HDFCBANK not available/, "and says not available for a gap"); }
re(fig(h, "pe"), /<title>TCS: 21\.00<\/title>/, "bar tooltips");
{ const g = fig(h, "level-revenue"); const ticks = [...g.matchAll(/text-anchor="end"[^>]*>([-0-9,.]+)</g)].map((m) => m[1]); ok(ticks.length >= 3, "axis labels are drawn"); ok(ticks[0] === "0" || ticks.includes("0"), "the axis includes zero"); }
{ const d = D(); d.val.stocks.forEach((s) => { s.pe = 123456789.123; }); const g = fig(build(["TCS", "INFY"], d), "pe"); re(g, /<svg/, "huge values still draw"); ok(names(fig(build(["TCS", "INFY", "RELIANCE"], d), "pe")).length === 3, "huge values with three stocks"); }
{ const d = D(); d.val.stocks.forEach((s) => { s.pe = 0; }); const g = fig(build(["TCS", "INFY"], d), "pe"); re(g, /<svg/, "all-zero values draw an empty axis, no division by zero"); no(g, /NaN|Infinity|undefined/, "no NaN"); eq(lblMap(g), { TCS: "0.00", INFY: "0.00" }, "zeros are shown as zeros"); }
no(h, /NaN|Infinity|undefined|null/, "no NaN, Infinity, undefined or null anywhere in the output");

// ================= 9. wording: descriptive only =================
const ADVICE = /\b(best|worst|top|cheap\w*|expensive|undervalued|overvalued|outperform\w*|underperform\w*|buy|sell|hold|strong\w*|weak\w*|better|worse|winner|loser|leader|laggard|rank\w*|rated?|rating|scor\w*|signals?|recommend\w*|target|should|advice|advise|bullish|bearish|favou?r\w*|attractive|safe|risky|opportunit\w*|momentum|trend\w*|predict\w*|forecast|outlook)\b/i;
for (const set of [["TCS", "INFY"], ["HDFCBANK", "TCS", "SBIN", "INFY"], ["HDFCBANK", "SBIN"], ["TCS", "INFY", "RELIANCE", "LT", "BHARTIARTL"], ["GAPPY", "EARLY", "NEGSTART", "FLAT"], ["TCS", "HDFCBANK"]]) {
  const hh = build(set), sec = hh.slice(hh.indexOf("data-ccharts-title"), hh.indexOf('<h3 style="margin:1.6em 0 .2em">Financial history</h3>'));
  no(text(sec), ADVICE, set.join(",") + ": no ranking, rating or advice wording in the chart section"); const attrs = [...sec.matchAll(/(?:aria-label|title)="([^"]*)"/g)].map((m) => m[1]).join(" "); no(attrs, ADVICE, "nor in any aria-label or tooltip"); const ttl = [...sec.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => m[1]).join(" "); no(ttl, ADVICE, "nor in bar tooltips"); }
{ const hh = build(["TCS", "INFY"]); const bb = bars(fig(hh, "pe")); ok(!/(highest|lowest|largest|smallest|first|last place|1st|2nd)/i.test(text(hh.slice(hh.indexOf("data-ccharts-title"), hh.indexOf("Financial history</h3>")))), "no highest/lowest labelling"); }
re(text(build(["TCS", "INFY"])), /One bar per stock, in the order the stocks were chosen\. Bars start at zero; an empty slot means the value is not available, and nothing is estimated\. Descriptive data only\./, "the section's own plain-language note");
re(text(build(["TCS", "INFY"])), /Side-by-side data only\. It is not a ranking, rating, recommendation or advice\./, "the closing disclaimer is still there");

// ================= 10. no new data, nothing else moved =================
eq((cmpCode.match(/fetch\(/g) || []).length, 1, "still one fetch call"); eq([...new Set(cmpCode.match(/out\/[a-z_]+\.json/g))].sort(), ["out/financial_history.json", "out/fundamentals.json", "out/historical.json"], "still the same three files");
no(cmpCode, /localStorage|sessionStorage|indexedDB|XMLHttpRequest|eval\(|document\.write|<script|https?:|xmlns|createChart|LightweightCharts|<canvas|<img|Math\.random|Date\.now|new Date|setInterval|WebSocket/, "no storage, no network address, no library, no canvas or image, no randomness or clock");
eq(PINBLOCKS.length, 7, "seven classic PINBLOCKS"); eq(PINHTML.split(FHTAG).length, 2, "one Financial History module"); eq(PINHTML.split(CMPTAG).length, 2, "one compare module"); eq((PINHTML.match(/<script/g) || []).length, 13, "thirteen script elements (Phase 5H.4 adds the shareholding module); previously twelve (Phase 5E adds the snapshot module, Phase 5F the search module)");
let base = ""; try { base = cp.execSync("git show 700767f:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
if (base) { const ob = base.split("<script>").slice(1).map((x) => x.split("</script>")[0]); eq(PINBLOCKS.filter((b) => !b.includes("Stock Detail view")).map(sha), ob.filter((b) => !b.includes("Stock Detail view")).map(sha), "the classic PINBLOCKS except the Stock Detail block (Phase 5D.1) are byte-identical to 700767f"); eq(sha(modOf(PINHTML, FHTAG)), sha(modOf(base, FHTAG)), "the Financial History module (and its charts) is byte-identical to 700767f");
  const strip = (x) => x.replace(/<script type="module" id="stocklens-compare">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-snapshot">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-shareholding">[\s\S]*?<\/script>\n/, ""); const ND = ((x) => x.split("<script>").map((q, i) => (i && q.split("</script>")[0].includes("Stock Detail view") ? "</script>" + q.split("</script>").slice(1).join("</script>") : q)).join("<script>")); eq(ND(strip(PINHTML)), ND(strip(base)), "everything outside the compare module (and the Stock Detail block, Phase 5D.1) is identical to 700767f"); }
no(cmpCode, /SLFinHistory\s*=|window\.SLFinHistory\.(build|chartSvg|chartSlots)\b/, "the compare module reads the history module's model/change/cagr only; it neither replaces nor calls its chart code");
console.log("Financial history compare chart tests passed (" + checks + " checks)");
