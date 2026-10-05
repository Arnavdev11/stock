// Run: node tests/test_financial_history_ui.js   (no network, no browser; stubs the DOM, MutationObserver, fetch and the chart library)
// Tests the Phase 4 Step 4E Financial History panel: fiscal-year columns from the real records, Revenue vs Total income, "-" for gaps,
// provenance and reconciliation markers, change/CAGR rules (including the ITC FY2022/FY2023 boundary), the unavailable state, placement
// under Stock Research, and that every other script block of the page is byte-for-byte what it was before this step.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);          // the seven classic blocks the other suites pin
const MARK = "Phase 4 Step 4E - Financial History", MOD = '<script type="module">';
const fhCode = (html.split(MOD)[1] || "").split("</script>")[0], detailCode = blocks.find((b) => b.includes("Stock Detail view")),
  chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart")), techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot")),
  researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research")), checkCode = blocks.find((b) => b.includes("Phase 4 Step 2 - Investment Checklist"));
assert.ok(fhCode.includes(MARK) && detailCode && chartCode && techCode && researchCode && checkCode, "script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();

// ---------- the panel's own pure API ----------
global.window = {}; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false });
const load = (code, name) => new Function(code.replace('"use strict";', "").replace("(function(){", "var window={location:{hash:\"\"}};(function(){").replace(/\}\)\(\);\s*$/, "})();return window." + name + ";"))();
const F = load(fhCode, "SLFinHistory");

// ---------- fixtures (tests only) ----------
const V = (o) => Object.assign({ revenue: null, total_revenue: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, eps_diluted: null, operating_cash_flow: null, investing_cash_flow: null, financing_cash_flow: null, capex: null, free_cash_flow: null }, o);
const R = (fy, values, { official = false, mismatch = false, basis = "consolidated" } = {}) => ({ fy, period: "Mar " + fy, basis, values: V(values),
  verification: { status: "verified", reconciliation: official ? "not_checked" : mismatch ? "mismatch" : "matched", warnings: [], checks: {} }, source: { provider: official ? "Official annual report" : "Upstox" }, revisions: [] });
const grow = (start, n, f = 1.1) => Array.from({ length: n }, (_, i) => +(start * Math.pow(f, i)).toFixed(4));
const series = (fy0, n, key, start, o = {}) => grow(start, n).map((v, i) => R(fy0 + i, { [key]: v, profit_before_tax: v / 2, profit_after_tax: v / 4, eps_basic: 10 + i, operating_cash_flow: v / 3 }, o));
const DOC = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox (API) and official company annual reports", stocks: {
  TCS: { symbol: "TCS", statement_layout: "standard", years: [R(2022, { revenue: 100, profit_before_tax: 50, profit_after_tax: 25, eps_basic: 10, operating_cash_flow: 33 }, { official: true }),
    R(2023, { revenue: 110, profit_before_tax: 55, profit_after_tax: 27.5, eps_basic: 11, operating_cash_flow: 36.5, investing_cash_flow: 5 }), R(2024, { revenue: 121, profit_before_tax: 60.5, profit_after_tax: 30.25, eps_basic: 12, operating_cash_flow: 40 }),
    R(2025, { revenue: 133.1, profit_before_tax: 66.55, profit_after_tax: 33.275, eps_basic: 13, operating_cash_flow: 44 }), R(2026, { revenue: 146.41, profit_before_tax: 73.205, profit_after_tax: 36.6025, eps_basic: 14, operating_cash_flow: 48 })].reverse() },
  INFY: { symbol: "INFY", statement_layout: "standard", years: series(2023, 4, "revenue", 200) },
  ITC: { symbol: "ITC", statement_layout: "standard", years: [R(2022, { revenue: 50, profit_before_tax: 20, profit_after_tax: 15, eps_basic: 12 }, { official: true }),
    ...[100, 110, 121, 133.1].map((v, i) => R(2023 + i, { revenue: v, profit_before_tax: v / 2, profit_after_tax: v / 4, eps_basic: 15 + i, operating_cash_flow: 9 }, { mismatch: true }))] },
  HDFCBANK: { symbol: "HDFCBANK", statement_layout: "financial", years: grow(1000, 4).map((v, i) => R(2023 + i, { total_revenue: v, profit_before_tax: v / 5, profit_after_tax: v / 8, eps_basic: 20 + i, operating_cash_flow: -50 * (i + 1) })) },
  NEGSTART: { symbol: "NEGSTART", statement_layout: "standard", years: [R(2023, { revenue: -5, profit_after_tax: -50 }), R(2024, { revenue: 10, profit_after_tax: 10 }), R(2025, { revenue: 20, profit_after_tax: 20 }), R(2026, { revenue: 40, profit_after_tax: 0 })] },
  GAP: { symbol: "GAP", statement_layout: "standard", years: [R(2023, { revenue: 10 }), R(2024, { revenue: 11 }), R(2026, { revenue: 14 })] },
  HOLE: { symbol: "HOLE", statement_layout: "standard", years: [R(2023, { revenue: 10 }), R(2024, { revenue: 11 }), R(2025, { revenue: null }), R(2026, { revenue: 14 })] },
  LATESTNULL: { symbol: "LATESTNULL", statement_layout: "standard", years: [R(2024, { revenue: 10 }), R(2025, { revenue: 11 }), R(2026, { revenue: null })] },
  TWOYR: { symbol: "TWOYR", statement_layout: "standard", years: [R(2025, { revenue: 100 }), R(2026, { revenue: 121 })] },
  SOLO: { symbol: "SOLO", statement_layout: "standard", years: [R(2026, { revenue: 100 })] },
  EMPTY: { symbol: "EMPTY", statement_layout: "standard", years: [] },
  MIXED: { symbol: "MIXED", statement_layout: "standard", years: [R(2025, { revenue: 1 }, { basis: "standalone" }), R(2026, { revenue: 2 }, { basis: "standalone" }), R(2026, { revenue: 99 })] } } });
const pageOf = (s, doc = DOC()) => F.build(s, doc);
const cell = (h, rowAttr, i) => { const m = h.match(new RegExp('<tr data-(?:row|chg)="' + rowAttr + '">(.*?)</tr>', "s")); const tds = [...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map((x) => text(x[1])); return tds[i]; };
const cells = (h, rowAttr) => { const m = h.match(new RegExp('<tr data-(?:row|chg)="' + rowAttr + '">(.*?)</tr>', "s")); return [...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map((x) => text(x[1])); };
const heads = (h) => [...h.match(/<thead>(.*?)<\/thead>/s)[1].matchAll(/<th>(.*?)<\/th>/g)].map((x) => text(x[1])).filter(Boolean);
const cagrLine = (h, key) => { const m = h.match(new RegExp('data-cagr="' + key + '">(.*?)</p>', "s")); return m ? text(m[1]) : "NOT FOUND"; };

// ================= 1. five-year TCS history (columns come from the records, newest-first input is sorted) =================
let h = pageOf("TCS");
eq(heads(h), ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"], "TCS: five fiscal-year columns, oldest first");
eq(cells(h, "revenue"), ["Revenue", "100", "110", "121", "133.1", "146.41"], "TCS revenue row");
eq(cells(h, "profit_before_tax").slice(1), ["50", "55", "60.5", "66.55", "73.21"], "TCS PBT row (shown to two decimals at most)");
eq(cells(h, "profit_after_tax").slice(1), ["25", "27.5", "30.25", "33.28", "36.6"], "TCS PAT row");
eq(cells(h, "eps_basic").slice(1), ["10.00", "11.00", "12.00", "13.00", "14.00"], "TCS EPS basic row (two decimals)");
eq(cells(h, "operating_cash_flow").slice(1), ["33", "36.5", "40", "44", "48"], "TCS operating cash flow row");
re(text(h), /Financial History/, "panel title"); re(text(h), /Consolidated · ₹ crore \(EPS in ₹\) · Source: Upstox \(API\) and official company annual reports · Updated: 2026-10-04/, "basis, units, source and date line");
eq(F.model(DOC(), "TCS").cols.length, 5, "the number of columns equals the number of records"); eq(F.model(DOC(), "INFY").cols.map((c) => c.fy), [2023, 2024, 2025, 2026], "columns are generated from the data, not fixed");
// a record added to the data produces a column; nothing is hard-coded
{ const d = DOC(); d.stocks.INFY.years.push(R(2027, { revenue: 999 })); eq(heads(pageOf("INFY", d)), ["FY2023", "FY2024", "FY2025", "FY2026", "FY2027"], "an extra year in the data adds a column"); }
{ const d = DOC(); d.stocks.TCS.years = d.stocks.TCS.years.filter((y) => y.fy !== 2024); eq(heads(pageOf("TCS", d)), ["FY2022", "FY2023", "FY2025", "FY2026"], "a missing year simply has no column (nothing manufactured)"); }
re(text(pageOf("TCS")), /Investing/i === null ? /x/ : /^(?!.*Investing cash flow)/, "only the five approved rows are shown (no investing/financing rows)");

// ================= 2. four-year stock with missing FY2022 =================
h = pageOf("INFY");
eq(heads(h), ["FY2023", "FY2024", "FY2025", "FY2026"], "INFY: four columns"); no(h, /FY2022/, "INFY: no FY2022 column is invented");
eq(cagrLine(h, "revenue"), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "INFY revenue CAGR over the three intervals of four years");

// ================= 3. ITC FY2022 / FY2023 comparability boundary =================
h = pageOf("ITC");
eq(heads(h), ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"], "ITC: five columns");
eq(cells(h, "profit_before_tax")[1], "20", "ITC FY2022 PBT is still shown as a value"); eq(cells(h, "profit_after_tax")[1], "15", "ITC FY2022 PAT is still shown as a value");
eq(cells(h, "profit_before_tax").slice(1, 3), ["20", "50"], "ITC PBT values around the boundary");
for (const k of ["revenue", "profit_before_tax", "profit_after_tax"]) { const c = cells(h, k); eq(c[1], k === "revenue" ? "50" : k === "profit_before_tax" ? "20" : "15", "FY2022 shown"); eq(c.length, 6, "ITC rows are complete"); }
const chg = (k) => { const m = h.match(new RegExp('<tr data-chg="' + k + '">(.*?)</tr>', "s")); return [...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map((x) => text(x[1])); };
eq(chg("profit_before_tax").slice(1), ["-", "-", "+10.00%", "+10.00%", "+10.00%"], "ITC PBT: FY2022 has no previous year, FY2022→FY2023 change is suppressed, later years are calculated");
eq(chg("profit_after_tax").slice(1), ["-", "-", "+10.00%", "+10.00%", "+10.00%"], "ITC PAT: FY2022→FY2023 change is suppressed");
eq(chg("revenue").slice(1)[1], "-", "ITC revenue FY2023 change is also not calculated across the boundary");
for (const k of ["revenue", "profit_before_tax", "profit_after_tax"]) eq(cagrLine(h, k).replace(/^[^·]+· /, ""), "3-yr CAGR (FY23–FY26): +10.00%", "ITC " + k + " CAGR starts at FY2023, never at FY2022");
no(h, /4-yr CAGR/, "no ITC CAGR spans the FY2022/FY2023 boundary"); re(text(h), /not calculated across FY2022 to FY2023 for ITC/, "the boundary is explained in neutral words");
eq(F.cagr(F.model(DOC(), "ITC"), "revenue").from, 2023, "model-level check: window starts FY2023");
no(pageOf("ITC"), /definition|presentation|restat|like-for-like|comparable/i, "the ITC note only says what is not calculated; it makes no claim about the source definitions"); no(fhCode, /one definition|same definition/i, "no wording that FY2022 and FY2023 are on one definition"); no(pageOf("TCS"), /not calculated across/, "no boundary note for stocks without a boundary"); no(pageOf("INFY"), /not calculated across/, "no boundary note for INFY");
{ const d = DOC(); d.stocks.ITC.years = d.stocks.ITC.years.filter((y) => y.fy >= 2023); no(pageOf("ITC", d), /not calculated across/, "no note when the data has no FY2022"); }
// the same data under a different symbol is NOT suppressed (the boundary is a defined exception, not a general rule)
// the ITC boundary is a defined exception, not a general rule: the same data under another symbol is not suppressed, and its window is the default FY2023-FY2026
{ const d = DOC(); d.stocks.OTHER = { ...d.stocks.ITC, symbol: "OTHER" }; const hh = pageOf("OTHER", d);
  eq(chg2("OTHER", "revenue", d), ["-", "+100.00%", "+10.00%", "+10.00%", "+10.00%"], "OTHER: FY2022→FY2023 change IS calculated (no boundary for other stocks)"); eq(cagrLine(hh, "revenue"), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "OTHER: default window FY2023-FY2026"); no(hh, /not calculated across/, "OTHER: no boundary note"); }

// ================= 4. bank: Total income, never Revenue =================
h = pageOf("HDFCBANK");
eq(cells(h, "total_revenue")[0], "Total income", "bank row label"); eq(cells(h, "total_revenue").slice(1), ["1,000", "1,100", "1,210", "1,331"], "bank Total income values");
no(h, /data-row="revenue"/, "bank has no Revenue row"); no(text(h).replace(/Total income/g, ""), /\bRevenue\b/, "the word Revenue does not appear for a bank");
eq(cagrLine(h, "total_revenue"), "Total income · 3-yr CAGR (FY23–FY26): +10.00%", "bank CAGR is labelled Total income");
re(text(h), /the first row is the bank's total income as reported/, "bank note"); no(pageOf("TCS"), /Total income/, "non-bank never says Total income");
{ const d = DOC(); d.stocks.TCS.years.forEach((y) => { y.values.total_revenue = 5555; }); ok(!/5,555/.test(pageOf("TCS", d)), "non-bank Revenue row never substitutes total_revenue"); d.stocks.TCS.years.forEach((y) => { y.values.revenue = null; }); eq(cells(pageOf("TCS", d), "revenue").slice(1), ["-", "-", "-", "-", "-"], "a missing Revenue shows '-' for a non-bank"); }

// ================= 5. CAGR calculation and year count =================
{ const m = F.model(DOC(), "TCS"), c = F.cagr(m, "revenue");
  eq([c.n, c.from, c.to], [4, 2022, 2026], "TCS: four intervals across five fiscal years"); ok(Math.abs(c.pct - 10) < 1e-9, "TCS: 100 → 146.41 over 4 years is 10.00%"); eq(c.label, "4-yr CAGR (FY22–FY26)", "period label");
  const i = F.cagr(F.model(DOC(), "INFY"), "revenue"); eq([i.n, i.from, i.to], [3, 2023, 2026], "four years of data = 3 intervals"); ok(Math.abs(i.pct - 10) < 1e-9, "INFY 10%");
  eq(F.cagr(F.model(DOC(), "TWOYR"), "revenue"), null, "two fiscal years are one interval: that is a change, not a CAGR");
  const t = F.cagr(F.model(DOC(), "INFY"), "profit_after_tax"); eq(t.n, 3, "three intervals"); ok(Math.abs(t.pct - 10) < 1e-9, "INFY PAT 10%");
  eq(cagrLine(pageOf("TCS"), "profit_before_tax"), "Profit before tax · 4-yr CAGR (FY22–FY26): +10.00%", "PBT CAGR shown"); eq(cagrLine(pageOf("TCS"), "profit_after_tax"), "Profit after tax · 4-yr CAGR (FY22–FY26): +10.00%", "PAT CAGR shown"); }
eq(cells(pageOf("TCS"), "revenue").length, 6, "row length"); eq(chg2("TCS", "revenue"), ["-", "+10.00%", "+10.00%", "+10.00%", "+10.00%"], "TCS revenue change vs previous year");
function chg2(s, k, doc) { const x = pageOf(s, doc).match(new RegExp('<tr data-chg="' + k + '">(.*?)</tr>', "s")); return [...x[1].matchAll(/<td[^>]*>(.*?)<\/td>/gs)].map((y) => text(y[1])).slice(1); }
// a decline is shown with its sign, not hidden
{ const d = DOC(); d.stocks.INFY.years = [R(2023, { revenue: 200 }), R(2024, { revenue: 180 }), R(2025, { revenue: 162 }), R(2026, { revenue: 145.8 })]; eq(cagrLine(pageOf("INFY", d), "revenue"), "Revenue · 3-yr CAGR (FY23–FY26): -10.00%", "a negative CAGR (positive values that fell) is shown with its sign"); }

// ================= 6. fixed CAGR windows; missing / non-positive values; change vs previous year =================
// windows: TCS FY2022-FY2026, ITC FY2023-FY2026, every other stock FY2023-FY2026. Never shortened, never substituted.
const cl = (s, k, doc) => cagrLine(pageOf(s, doc), k);
eq(cl("TCS", "revenue"), "Revenue · 4-yr CAGR (FY22–FY26): +10.00%", "TCS window is FY2022→FY2026"); eq(cl("ITC", "revenue"), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "ITC window is FY2023→FY2026");
eq(cl("INFY", "revenue"), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "other stocks: FY2023→FY2026"); eq(cl("HDFCBANK", "total_revenue"), "Total income · 3-yr CAGR (FY23–FY26): +10.00%", "banks: FY2023→FY2026");
{ // TCS without FY2022: NOT shortened to FY2023-FY2026
  const d = DOC(); d.stocks.TCS.years = d.stocks.TCS.years.filter((y) => y.fy !== 2022); eq(cl("TCS", "revenue", d), "Revenue · CAGR: -", "TCS missing FY2022: '-' (no 3-yr substitute)"); eq(F.cagr(F.model(d, "TCS"), "revenue"), null, "TCS missing FY2022: null"); }
{ // TCS missing a middle year / value
  const d = DOC(); d.stocks.TCS.years = d.stocks.TCS.years.filter((y) => y.fy !== 2024); eq(cl("TCS", "revenue", d), "Revenue · CAGR: -", "TCS missing FY2024: '-'");
  const e = DOC(); e.stocks.TCS.years.find((y) => y.fy === 2025).values.revenue = null; eq(cl("TCS", "revenue", e), "Revenue · CAGR: -", "TCS FY2025 value null: '-'"); }
{ // TCS missing the latest year: no older end point is used
  const d = DOC(); d.stocks.TCS.years = d.stocks.TCS.years.filter((y) => y.fy !== 2026); eq(cl("TCS", "revenue", d), "Revenue · CAGR: -", "TCS missing FY2026: '-' (not FY2022→FY2025)"); }
{ // ITC without FY2023: not started at FY2024
  const d = DOC(); d.stocks.ITC.years = d.stocks.ITC.years.filter((y) => y.fy !== 2023); eq(cl("ITC", "revenue", d), "Revenue · CAGR: -", "ITC missing FY2023: '-' (no FY2024 start, no FY2022 start)");
  const e = DOC(); e.stocks.ITC.years = e.stocks.ITC.years.filter((y) => y.fy !== 2025); eq(cl("ITC", "revenue", e), "Revenue · CAGR: -", "ITC missing FY2025: '-'");
  const f = DOC(); f.stocks.ITC.years = f.stocks.ITC.years.filter((y) => y.fy !== 2022); eq(cl("ITC", "revenue", f), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "ITC without FY2022 still has its FY2023-FY2026 window"); }
{ // another stock: FY2023 missing -> '-' (a FY2024 start is never substituted); extra FY2022 is not used
  const d = DOC(); d.stocks.INFY.years = d.stocks.INFY.years.filter((y) => y.fy !== 2023); eq(cl("INFY", "revenue", d), "Revenue · CAGR: -", "INFY missing FY2023: '-' (2-yr substitute not calculated)");
  const e = DOC(); e.stocks.INFY.years.push(R(2022, { revenue: 50 })); eq(cl("INFY", "revenue", e), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "an extra FY2022 record does not lengthen the window");
  const f = DOC(); f.stocks.INFY.years = f.stocks.INFY.years.filter((y) => y.fy !== 2026); eq(cl("INFY", "revenue", f), "Revenue · CAGR: -", "INFY missing FY2026: '-'");
  const g = DOC(); g.stocks.INFY.years.push(R(2027, { revenue: 999 })); eq(cl("INFY", "revenue", g), "Revenue · 3-yr CAGR (FY23–FY26): +10.00%", "a later year does not move the window"); }
eq(cl("GAP", "revenue"), "Revenue · CAGR: -", "a missing fiscal year in the window: '-' (never bridged)"); eq(heads(pageOf("GAP")), ["FY2023", "FY2024", "FY2026"], "no FY2025 column is manufactured");
eq(cl("HOLE", "revenue"), "Revenue · CAGR: -", "a null inside the window: '-'"); eq(cells(pageOf("HOLE"), "revenue").slice(1), ["10", "11", "-", "14"], "the hole is shown as '-'");
eq(cl("LATESTNULL", "revenue"), "Revenue · CAGR: -", "latest year null / FY2023 missing: '-'"); eq(cl("SOLO", "revenue"), "Revenue · CAGR: -", "one year: '-'"); eq(cl("TWOYR", "revenue"), "Revenue · CAGR: -", "two years: '-' (no shorter substitute)");
// non-positive values anywhere in the window
h = pageOf("NEGSTART");
eq(cagrLine(h, "revenue"), "Revenue · CAGR: -", "negative start value: '-'"); eq(cagrLine(h, "profit_after_tax"), "Profit after tax · CAGR: -", "zero end value: '-'");
eq(F.cagr(F.model(DOC(), "NEGSTART"), "profit_after_tax"), null, "zero end → null"); eq(F.cagr(F.model(DOC(), "NEGSTART"), "revenue"), null, "negative start → null");
{ const d = DOC(); d.stocks.INFY.years.find((y) => y.fy === 2025).values.revenue = -1; eq(cl("INFY", "revenue", d), "Revenue · CAGR: -", "a negative value in the middle of the window: '-'");
  const e = DOC(); e.stocks.INFY.years.find((y) => y.fy === 2025).values.revenue = 0; eq(cl("INFY", "revenue", e), "Revenue · CAGR: -", "a zero in the middle of the window: '-'");
  const f = DOC(); f.stocks.INFY.years.find((y) => y.fy === 2026).values.revenue = 0; eq(cl("INFY", "revenue", f), "Revenue · CAGR: -", "a zero end value: '-'"); }
// the rule is per metric: PBT can be computable while revenue is not
{ const d = DOC(); d.stocks.INFY.years.find((y) => y.fy === 2023).values.revenue = null; const hh = pageOf("INFY", d); eq(cagrLine(hh, "revenue"), "Revenue · CAGR: -", "revenue: '-'"); eq(cagrLine(hh, "profit_before_tax"), "Profit before tax · 3-yr CAGR (FY23–FY26): +10.00%", "PBT still computed from its own complete window"); }
eq(cells(pageOf("GAP"), "revenue").length, 4, "row length");

// ---- change vs previous year: consecutive years + both values present; a negative or zero previous value does NOT suppress it ----
eq(chg2("NEGSTART", "revenue"), ["-", "+300.00%", "+100.00%", "+100.00%"], "previous value negative (-5 → 10): the change is calculated, measured against the size of the old value");
eq(chg2("NEGSTART", "profit_after_tax"), ["-", "+120.00%", "+100.00%", "-100.00%"], "-50 → 10 is +120%; 10 → 20 is +100%; 20 → 0 is -100% (the end value may be zero)");
{ const d = DOC(); d.stocks.INFY.years.find((y) => y.fy === 2024).values.revenue = 0; eq(chg2("INFY", "revenue", d), ["-", "-100.00%", "-", "+10.00%"], "a previous value of exactly 0 has no percentage ('-'); into 0 is -100%; out of it resumes"); }
{ const d = DOC(); d.stocks.INFY.years.find((y) => y.fy === 2025).values.revenue = null; eq(chg2("INFY", "revenue", d), ["-", "+10.00%", "-", "-"], "a null on either side: '-'"); }
{ const d = DOC(); d.stocks.INFY.years.find((y) => y.fy === 2025).values.revenue = -20; eq(chg2("INFY", "revenue", d), ["-", "+10.00%", "-109.09%", "+1431.00%"], "positive → negative is shown (not hidden), and negative → positive is shown, measured against the size of the old value"); }
eq(chg2("GAP", "revenue"), ["-", "+10.00%", "-"], "a change across a missing year is not calculated"); eq(chg2("SOLO", "revenue"), ["-"], "one year: no change");
eq(chg2("ITC", "revenue"), ["-", "-", "+10.00%", "+10.00%", "+10.00%"], "ITC FY2022→FY2023 is the only suppressed change");
{ // after the boundary ITC changes are calculated normally, including from a negative previous value
  const d = DOC(); d.stocks.ITC.years.find((y) => y.fy === 2024).values.profit_after_tax = -10; eq(chg2("ITC", "profit_after_tax", d), ["-", "-", "-140.00%", "+402.50%", "+10.00%"], "ITC: only FY2022→FY2023 is suppressed; the rest are calculated, even from a negative value"); }
ok(Math.abs(F.change(F.model(DOC(), "TCS"), 1, "revenue") - 10) < 1e-9, "model-level: change() 100 → 110 is 10%"); eq(F.change(F.model(DOC(), "ITC"), 1, "revenue"), null, "model-level: ITC FY2022→FY2023 is null");
eq(heads(pageOf("MIXED")), ["FY2026"], "one basis only"); eq(cells(pageOf("MIXED"), "revenue").slice(1), ["99"], "bases are never mixed: the consolidated record wins and standalone is ignored"); no(pageOf("MIXED"), /Standalone/, "consolidated shown");
{ const d = DOC(); d.stocks.MIXED.years = d.stocks.MIXED.years.filter((y) => y.basis === "standalone"); eq(heads(pageOf("MIXED", d)), ["FY2025", "FY2026"], "if only standalone exists it is shown, labelled Standalone"); re(text(pageOf("MIXED", d)), /Standalone/, "standalone label"); }

// ================= 7. no EPS growth, no cash-flow growth, no other growth =================
h = pageOf("TCS");
eq([...h.matchAll(/data-chg="([a-z_]+)"/g)].map((m) => m[1]), ["revenue", "profit_before_tax", "profit_after_tax"], "change rows exist only for revenue, PBT and PAT");
eq([...h.matchAll(/data-cagr="([a-z_]+)"/g)].map((m) => m[1]), ["revenue", "profit_before_tax", "profit_after_tax"], "CAGR lines exist only for revenue, PBT and PAT");
const body = text(h).replace(/EPS and cash flow are shown per year only; no growth is calculated for them\./, ""); no(body, /EPS[^.]*(growth|CAGR|change)/i, "no EPS growth text"); no(body, /cash flow[^.]*(growth|CAGR|change vs)/i, "no cash-flow growth text");
re(text(h), /EPS and cash flow are shown per year only; no growth is calculated for them/, "the rule is stated");
eq(F.model(DOC(), "TCS").metrics.filter((m) => m.growth).map((m) => m.key), ["revenue", "profit_before_tax", "profit_after_tax"], "model: growth flag only on the three approved metrics");
eq(F.model(DOC(), "TCS").metrics.map((m) => m.key), ["revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "model: the five approved rows in order");

// ================= 8. reconciliation mismatch marker and official-source marker =================
h = pageOf("ITC");
eq(cells(h, "source"), ["Source notes", "Official annual report", "Source summary differs", "Source summary differs", "Source summary differs", "Source summary differs"], "ITC: official marker on FY2022, mismatch marker on the Upstox years");
re(text(h), /Source summary differs: the source's own summary lines did not agree/, "mismatch explanation, neutral"); re(text(h), /Official annual report: the figures for that year were taken from the company's annual report, not from Upstox/, "official explanation, neutral");
eq(cells(pageOf("TCS"), "source"), ["Source notes", "Official annual report", "", "", "", ""], "TCS: only FY2022 is marked official, no mismatch marker when the status is matched");
no(pageOf("INFY"), /Official annual report/, "no official marker when no record is official"); no(pageOf("INFY"), /Source summary differs/, "no mismatch marker when every year matched");
eq(cells(pageOf("HDFCBANK"), "source"), ["Source notes", "", "", "", ""], "no markers where there is nothing to mark");
{ const d = DOC(); d.stocks.INFY.years[1].verification.reconciliation = "mismatch"; eq(cells(pageOf("INFY", d), "source"), ["Source notes", "", "Source summary differs", "", ""], "the marker follows the ledger status per year"); }
no(text(pageOf("ITC")), /\b(error|wrong|warning|bad|unreliable|incorrect)\b/i, "markers are neutral: no alarm words");
{ const d = DOC(); d.stocks.TCS.years.forEach((y) => { y.source = { provider: "Upstox" }; }); no(pageOf("TCS", d), /Official annual report/, "the official marker comes from the provider field only"); }

// ================= 9. unavailable / missing data =================
eq(text(F.build("TCS", null)), "Financial History Financial history unavailable.", "no document: 'Financial history unavailable.'");
for (const bad of [undefined, "x", 5, [], {}, { stocks: [] }, { stocks: "x" }]) re(text(F.build("TCS", bad)), /Financial history unavailable\./, "malformed document: " + JSON.stringify(bad));
re(text(F.build("NOPE", DOC())), /No financial history is on file for NOPE\./, "a stock that is not in the file"); re(text(F.build("EMPTY", DOC())), /No financial history is on file for EMPTY\./, "a stock with no years");
re(text(F.build("constructor", DOC())), /No financial history is on file/, "prototype keys are not stocks"); { const raw = F.build("<img src=x>", DOC()); re(raw, /&lt;img src=x&gt;/, "a symbol is escaped"); no(raw, /<img/, "no raw tag from a symbol"); }
{ const d = DOC(); d.stocks.TCS.years = [null, 5, { fy: "2024", values: {} }, { fy: 2025 }, R(2026, { revenue: 3 })]; eq(heads(pageOf("TCS", d)), ["FY2026"], "malformed records are skipped, not guessed"); }
{ const d = DOC(); d.stocks.TCS.years = [R(2026, { revenue: "12", profit_after_tax: NaN, eps_basic: Infinity, profit_before_tax: null })]; eq([cell(pageOf("TCS", d), "revenue", 1), cell(pageOf("TCS", d), "profit_after_tax", 1), cell(pageOf("TCS", d), "eps_basic", 1), cell(pageOf("TCS", d), "profit_before_tax", 1)], ["-", "-", "-", "-"], "non-numbers show '-' (a string is never parsed into a number)"); }
{ const d = DOC(); d.stocks.TCS.years.forEach((y) => { y.values.profit_after_tax = 0; }); eq(cells(pageOf("TCS", d), "profit_after_tax").slice(1), ["0", "0", "0", "0", "0"], "a real zero is shown as 0"); }
// nothing is stored in the doc by rendering
{ const d = DOC(), before = JSON.stringify(d); pageOf("TCS", d); pageOf("ITC", d); eq(JSON.stringify(d), before, "rendering never changes the data"); }
no(text(pageOf("TCS")), /\b(buy|sell|hold|recommend|undervalued|overvalued|rating|score|signal|target|forecast|outlook|strong|weak)\b/i, "no advice, rating, score or signal wording");

// ================= 10. the page: placement, loading, error state, existing functionality =================
const day = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
const candlesA = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const benchA = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 20000 + i, high: 20001 + i, low: 19999 + i, close: 20000 + i, volume: 0 }));
const HIST = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: benchA() }, stocks: { TCS: { symbol: "TCS", candles: candlesA() }, ITC: { symbol: "ITC", candles: candlesA() }, HDFCBANK: { symbol: "HDFCBANK", candles: candlesA() } } };
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: ["TCS", "ITC", "HDFCBANK"].map((s) => ({ symbol: s, company_name: s + " Ltd", sector: "Test", pe: 25.5, pb: 12.3, roa: 20.1, roe: 51.2, roce: 60.4, ev_ebitda: 18.7 })) };
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: ["TCS", "ITC", "HDFCBANK"].map((s) => ({ symbol: s, basis: "consolidated", income: { period: "Mar 2026", basis: "consolidated", revenue: 250000.5, total_revenue: 255000.25, profit_before_tax: 62000, profit_after_tax: 48000.75 }, balance_sheet: { period: "Mar 2026", total_assets: 150000, total_liabilities: 60000, total_equity: 90000 }, cash_flow: { period: "Mar 2026", operating: 55000, investing: -20000, financing: -30000, free_cash_flow: null } })) };
function stubLib(log) {
  const mk = (kind) => { const s = { kind, data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; return s; };
  return { createChart(el, opts) { const ch = { el, opts, series: [], removed: false, addCandlestickSeries() { const s = mk("c"); ch.series.push(s); return s; }, addLineSeries() { const s = mk("l"); ch.series.push(s); return s; }, addHistogramSeries() { const s = mk("h"); ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }; }, remove() { ch.removed = true; } }; log.push(ch); return ch; } };
}
async function boot(hash, { fh = DOC(), delay = 0, settle = null, extra = true } = {}) {
  const els = {}, win = {}, charts = [], fetched = [], observers = [];
  let boxHtml = "", children = [];
  const sched = () => queueMicrotask(() => observers.forEach((f) => f([])));
  const box = { id: "detail", get innerHTML() { return boxHtml + children.map((c) => c.innerHTML).join(""); }, set innerHTML(v) { boxHtml = v; children.forEach((c) => { c.parentNode = null; }); children = []; sched(); },
    appendChild(c) { c.parentNode = box; children.push(c); sched(); return c; },
    insertBefore(c, ref) { c.parentNode = box; const i = ref ? children.indexOf(ref) : -1; if (i < 0) children.push(c); else children.splice(i, 0, c); sched(); return c; }, setAttribute() {}, addEventListener() {}, querySelector: () => null };
  const mk = (id) => { const e = { id, innerHTML: "", textContent: "", attrs: {}, setAttribute(k, v) { e.attrs[k] = v; }, addEventListener() {}, querySelector: () => null }; return e; };
  global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) observers.push(cb); }; };
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {}, LightweightCharts: stubLib(charts) };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } },
    getElementById: (i) => (i === "detail" ? box : children.find((c) => c.id === i) || els[i] || (els[i] = mk(i))), addEventListener() {}, querySelector: () => null,
    createElement: () => { const c = { parentNode: null, innerHTML: "", id: "" }; Object.defineProperty(c, "nextSibling", { get() { const i = children.indexOf(c); return i >= 0 ? children[i + 1] || null : null; } }); return c; }, head: { appendChild() {} } };
  const files = { "fundamentals.json": FUND, "financials.json": FIN, "historical.json": HIST, "financial_history.json": fh };
  global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); if (fh === "reject" && u.endsWith("financial_history.json")) throw new Error("network");
    const f = files[u.split("/").pop()] ?? (u.endsWith("scans.json") ? { as_of: "2026-10-01", source: "NSE", stocks: [] } : null); return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(detailCode); eval(chartCode); eval(techCode); eval(researchCode); eval(checkCode); if (extra) eval(fhCode);
  await sleep(settle ?? 60 + delay * 3);
  const nav = async (h2, ms = 60 + delay * 3) => { window.location.hash = h2; win.hashchange(); await sleep(ms); };
  return { els, charts, fetched, nav, win, ids: () => children.map((c) => c.id), child: (id) => (children.find((c) => c.id === id) || {}).innerHTML || "", detailHtml: () => boxHtml };
}
(async () => {
  const base = await boot("#stock=TCS", { extra: false }); const baseRes = base.child("detailResearch"), baseChk = base.child("detailChecklist"), baseDet = base.detailHtml(), baseIds = base.ids();
  let t = await boot("#stock=TCS");
  eq(t.ids(), ["detailResearch", "detailFinancialHistory", "detailChecklist"], "the panel sits directly under Stock Research and above the checklist");
  h = t.child("detailFinancialHistory"); eq(heads(h), ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"], "the page shows the five TCS columns"); eq(cagrLine(h, "revenue"), "Revenue · 4-yr CAGR (FY22–FY26): +10.00%", "and the CAGR line");
  re(t.child("detailResearch"), /Stock Research/, "Stock Research is still there"); re(t.child("detailResearch"), /Fundamental Quality/, "with its sections"); re(t.child("detailChecklist"), /Investment Checklist/, "the checklist is still there");
  re(t.detailHtml(), /id="detailChart"/, "the Stock Detail chart container is still rendered"); ok(t.charts.length > 0, "the chart is still drawn"); re(t.detailHtml(), /Technical Snapshot|detailTech/, "the Technical Snapshot container is still rendered");
  eq(t.fetched.filter((u) => /financial_history/.test(u)), ["out/financial_history.json"], "exactly one request for the history file");
  ok(t.fetched.every((u) => /^out\/(fundamentals|financials|historical|scans|company_profiles|financial_history)\.json$/.test(u)), "only static files under out/ are requested: " + [...new Set(t.fetched)].join(", "));
  ok(!t.fetched.some((u) => /upstox|http/i.test(u)), "the browser never calls Upstox or any outside address");
  // identical page without the new block: every other piece is unchanged
  eq(baseIds, ["detailResearch", "detailChecklist"], "without the new block the page has exactly the two earlier sections"); eq(baseRes, t.child("detailResearch"), "Stock Research output is identical with and without the panel"); eq(baseChk, t.child("detailChecklist"), "Investment Checklist output is identical with and without the panel"); eq(baseDet, t.detailHtml(), "Stock Detail output is identical with and without the panel");
  // bank and ITC through the whole page
  await t.nav("#stock=HDFCBANK"); eq(t.ids(), ["detailResearch", "detailFinancialHistory", "detailChecklist"], "navigation replaces the panel (still one of each)"); eq(cells(t.child("detailFinancialHistory"), "total_revenue")[0], "Total income", "bank page shows Total income"); no(t.child("detailFinancialHistory"), /data-row="revenue"/, "bank page has no Revenue row");
  await t.nav("#stock=ITC"); re(text(t.child("detailFinancialHistory")), /not calculated across FY2022 to FY2023 for ITC/, "ITC page shows the boundary note"); re(t.child("detailFinancialHistory"), /Source summary differs/, "ITC page shows the mismatch marker");
  await t.nav("#stock=TCS"); no(t.child("detailFinancialHistory"), /Total income|ITC/, "back on TCS: no data from the earlier stocks"); eq(t.ids().filter((i) => i === "detailFinancialHistory").length, 1, "one panel");
  await t.nav(""); const nb = t.fetched.length; ok(t.ids().length <= 3, "leaving the stock view adds nothing"); eq(t.fetched.length, nb, "leaving the stock makes no request");
  t = await boot(""); eq(t.ids(), [], "dashboard: no panel"); ok(!t.fetched.some((u) => /financial_history/.test(u)), "dashboard: the history file is not requested");

  // ---- missing / failed history file: Stock Detail, Research and Checklist still work ----
  for (const [label, fh] of [["file missing (not ok)", null], ["request fails", "reject"], ["empty object", {}]]) {
    t = await boot("#stock=TCS", { fh }); re(text(t.child("detailFinancialHistory")), /Financial history unavailable\./, label + ": the panel says 'Financial history unavailable.'");
    no(t.child("detailFinancialHistory"), /<table/, label + ": no table"); eq(t.ids(), ["detailResearch", "detailFinancialHistory", "detailChecklist"], label + ": the other sections are still placed");
    re(t.child("detailResearch"), /Fundamental Quality/, label + ": Stock Research intact"); re(t.child("detailChecklist"), /Investment Checklist/, label + ": Checklist intact"); re(t.detailHtml(), /id="detailChart"/, label + ": Stock Detail intact"); ok(t.charts.length > 0, label + ": chart drawn");
  }
  t = await boot("#stock=TCS", { fh: { schema: 1, as_of: "x", source: "y", stocks: {} } }); re(text(t.child("detailFinancialHistory")), /No financial history is on file for TCS\./, "a file without this stock says so, neutrally");
  // a slow file never paints over a different stock
  t = await boot("#stock=TCS", { delay: 100, settle: 150 }); re(t.child("detailFinancialHistory"), /Loading/, "still loading"); await t.nav("#stock=HDFCBANK", 600); no(t.child("detailFinancialHistory"), /FY2022/, "a late answer for TCS is not painted onto HDFCBANK"); eq(cells(t.child("detailFinancialHistory"), "total_revenue")[0], "Total income", "HDFCBANK is shown after the stale answer");

  // ---- every other script block is exactly what it was ----
  eq(blocks.length, 7, "the page still has exactly the seven classic script blocks the other suites pin"); eq(html.split(MOD).length, 2, "the new code is exactly one separate module script");
  let headHtml = ""; try { headHtml = cp.execSync("git show HEAD:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { headHtml = ""; }
  if (headHtml) { const hb = headHtml.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
    eq(blocks.filter((b) => !b.includes("Stock Detail view")).map(sha), hb.filter((b) => !b.includes("Stock Detail view")).map(sha), "every classic script block except the Stock Detail block (Phase 5D.1) is byte-for-byte identical to the committed page");
    const strip = (x) => x.replace(/<script type="module" id="stocklens-compare">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-snapshot">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, "").replace(/<script type="module">\n\/\* StockLens Phase 4 Step 4E[\s\S]*?<\/script>\n/, "");
    const ND = ((x) => x.split("<script>").map((q, i) => (i && q.split("</script>")[0].includes("Stock Detail view") ? "</script>" + q.split("</script>").slice(1).join("</script>") : q)).join("<script>")); eq(ND(strip(html)), ND(strip(headHtml)), "everything outside the new module script (and the Stock Detail block, Phase 5D.1) is identical to the committed page"); }
  eq((fhCode.match(/fetch\(/g) || []).length, 1, "the new code makes exactly one fetch call"); eq([...new Set(fhCode.match(/out\/[a-z_]+\.json/g))], ["out/financial_history.json"], "and reads only the history file");
  no(fhCode, /localStorage|sessionStorage|indexedDB|XMLHttpRequest|eval\(|innerHTML\s*\+=/, "no storage, no eval, no unsafe patterns");
  console.log("Financial history UI tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
