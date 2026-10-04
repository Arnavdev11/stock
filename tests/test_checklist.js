// Run: node tests/test_checklist.js   (no network, no browser; stubs the DOM, MutationObserver, fetch and the chart library)
// Tests the Phase 4 Step 2 Investment Checklist: one status word per row (Available / Unavailable / Needs review / Data insufficient),
// "-" and "N/A" rules, debt and free cash flow never inferred, reuse of the existing technical calculations, placement after Stock Research,
// routing, wording (no advice, no score, no tally) and isolation of every other script block.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const checkCode = blocks.find((b) => b.includes("Phase 4 Step 2 - Investment Checklist"));
const researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research"));
const detailCode = blocks.find((b) => b.includes("Stock Detail view"));
const chartCode = blocks.find((b) => b.includes("Phase 3 Step 2 - historical price chart"));
const techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot"));
assert.ok(checkCode && researchCode && detailCode && chartCode && techCode, "script blocks exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- the real modules, loaded as pure APIs on a shared window ----------
global.window = { location: { hash: "" } }; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false }); delete global.MutationObserver;
new Function(techCode)(); new Function(chartCode)(); new Function(researchCode)(); new Function(checkCode)();
const L = window.SLChecklist, S = window.SLChart;

// ---------- fixtures (tests only) ----------
const day = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
const cs = (n) => Array.from({ length: n }, (_, i) => ({ date: day("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const bench = (n) => Array.from({ length: n }, (_, i) => ({ date: day("2025-01-01", i), open: 20000 + i, high: 20001 + i, low: 19999 + i, close: 20000 + i, volume: 0 }));
const stocks = {}; [0, 1, 19, 20, 29, 30, 33, 34, 49, 50, 199, 200, 400].forEach((n) => { if (n) stocks["N" + n] = { symbol: "N" + n, candles: cs(n) }; });
stocks.TCS = { symbol: "TCS", candles: cs(400) };
const HIST = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: bench(400) }, stocks };
const fund = (symbol, o = {}) => Object.assign({ symbol, company_name: symbol + " Ltd", sector: "Sector", pe: 25.5, pb: 12.3, roa: 20.1, roe: 51.2, roce: 60.4, ev_ebitda: 18.7 }, o);
const fin = (symbol, o = {}) => Object.assign({ symbol, basis: "consolidated",
  income: { period: "Mar 2026", basis: "consolidated", revenue: 250000.5, total_revenue: 255000.25, profit_before_tax: 62000, profit_after_tax: 48000.75, total_revenue_growth: 6.3, profit_after_tax_growth: 5.8 },
  balance_sheet: { period: "Mar 2026", basis: "consolidated", total_assets: 150000, total_liabilities: 60000, total_equity: 90000, liabilities_to_equity: 0.67, total_debt: null, debt_to_equity: null },
  cash_flow: { period: "Mar 2026", basis: "consolidated", operating: 55000, investing: -20000, financing: -30000, free_cash_flow: null, capex_status: "none" } }, o);
const bs = (o) => ({ period: "Mar 2026", basis: "consolidated", total_assets: 150000, total_liabilities: 60000, total_equity: 90000, liabilities_to_equity: 0.67, total_debt: null, debt_to_equity: null, ...o });
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: [
  fund("TCS"), fund("EQNEG"), fund("EQMIS"), fund("MIXED"), fund("DEBT"), fund("FCFV"), fund("NEGVAL", { pe: -5, pb: 0, ev_ebitda: 12 }),
  fund("MISS", { company_name: null, sector: null, pe: null, pb: null, roe: null, roce: null, roa: null, ev_ebitda: null }), fund("BADNUM", { pe: "abc", roe: NaN }),
  fund("HDFCBANK", { sector: "Financial Services" }), fund("NAMEXSS", { company_name: "Infosys & Co <Ltd>" }), fund("N400"), fund("N19"), fund("N29"), fund("N30"), fund("N33"), fund("N34"), fund("N49"), fund("N50"), fund("N199"), fund("N200"), fund("N20"), fund("N1")] };
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: [
  fin("TCS"),
  fin("EQNEG", { balance_sheet: bs({ total_assets: 1000, total_liabilities: 1500, total_equity: -500, liabilities_to_equity: -3 }) }),
  fin("EQMIS", { balance_sheet: bs({ total_assets: 1000, total_liabilities: 400, total_equity: 700, liabilities_to_equity: 0.57 }) }),
  fin("MIXED", { basis: "mixed" }),
  fin("DEBT", { balance_sheet: bs({ total_debt: 500, debt_to_equity: 0.8 }) }),
  fin("FCFV", { cash_flow: { period: "Mar 2026", basis: "consolidated", operating: 100, investing: -40, financing: -10, free_cash_flow: 1234.5, capex_status: "found" } }),
  { symbol: "MISS", basis: "consolidated", income: { period: "Mar 2026" }, balance_sheet: {}, cash_flow: {} },
  fin("NEARZERO", { balance_sheet: bs({ total_assets: 100, total_liabilities: 60, total_equity: 40.005 }) }),
  fin("EQEXACT", { balance_sheet: bs({ total_assets: 100, total_liabilities: 60, total_equity: 40.02 }) }),
  fin("EQZERO", { balance_sheet: bs({ total_assets: 100, total_liabilities: 100, total_equity: 0, liabilities_to_equity: 5 }) }),
  fin("LEMISS", { balance_sheet: bs({ liabilities_to_equity: null }) }),
  fin("HDFCBANK", { balance_sheet: bs({ total_assets: 1000000, total_liabilities: 900000, total_equity: 100000, liabilities_to_equity: 9 }) })] };
const D = (o = {}) => Object.assign({ val: FUND, fin: FIN, hist: HIST }, o);
const row = (sym, title, label, d = D()) => { const sec = L.sections(sym, d).find((x) => x.title === title); const r = sec && sec.rows.find((x) => x.l === label); return r ? { v: r.v, st: r.st, note: r.note } : null; };
const st = (sym, title, label, d) => { const r = row(sym, title, label, d); return r ? r.st : "ROW NOT FOUND"; };
const E = (title) => new Map();  // placeholder to keep the section helpers below readable

(async () => {
  // ================= 1. block structure and protection =================
  eq(blocks.length, 7, "seven script blocks: dashboard, detail, chart, snapshot, research, checklist, Phase 2B tabs");
  eq(blocks.indexOf(researchCode), 4, "Stock Research block is 5th, unchanged position"); eq(blocks.indexOf(checkCode), 5, "Investment Checklist block is 6th, right after Stock Research");
  ok(blocks.indexOf(checkCode) < blocks.length - 1, "checklist block is before the final Phase 2B block");
  eq(sha(blocks[0]), "3ee2ef6f101ccd3cd0df60bbd0bd37008c977e49c02e0cb3ba9caf8d128e1e5c", "dashboard block byte-identical");
  eq(sha(detailCode), "ec1d3d5d5791202fea1aa3d77e1189bb7fce41533ed529d98e35998ec8a1de77", "Stock Detail block byte-identical");
  eq(sha(chartCode), "ef5a348496d6aafa87c6352665fd3475d56d0bd778e73c062af759ec7b9dfc53", "chart + MACD block byte-identical");
  eq(sha(techCode), "5f187e38d73cb15eed203fbc0cc41deb7a88dce219c6eee6f55b6f99e719b339", "Technical Snapshot block byte-identical");
  eq(sha(researchCode), "94a757e0f14a71b76e2ea101a2b9d46c6756294a0d833170f2819a351c9a171a", "Stock Research block byte-identical (Step 1 untouched)");
  eq(sha(blocks[blocks.length - 1]), "35de0d215ec3947da870f95e636f41bf4b130d6d929be4d91d3deb2661344d2f", "final Phase 2B block byte-identical");
  re(checkCode, /window\.SLChecklist=/, "checklist module exposes its API");

  // ================= 2. section order, rows and the exact heading text =================
  const secs = L.sections("TCS", D());
  eq(secs.map((x) => x.title), ["Business", "Fundamental Quality", "Financial Health", "Cash Flow", "Valuation", "Technical"], "six sections in the approved order");
  eq(secs.map((x) => x.rows.map((r) => r.l)), [
    ["Company name", "Sector", "Business description"],
    ["Revenue", "Total revenue", "Total revenue growth", "Profit before tax", "Profit after tax", "Profit after tax growth", "ROE", "ROCE", "ROA"],
    ["Total assets", "Total liabilities", "Total equity", "Liabilities / Equity", "Debt / Equity", "Total debt"],
    ["Operating cash flow", "Investing cash flow", "Financing cash flow", "Free cash flow"],
    ["P/E", "P/B", "EV/EBITDA"],
    ["Last close", "Return 1M", "Return 3M", "Return 6M", "Return 1Y", "20 DMA (close vs average)", "50 DMA (close vs average)", "200 DMA (close vs average)", "RSI(14)", "MACD line", "MACD 9-day EMA line", "MACD histogram", "Relative Strength vs Nifty 50 (1Y)"]],
    "every approved row, in order");
  const built = L.build("TCS", D());
  ok(built.startsWith('<h3 style="margin:1.4em 0 .4em">Investment Checklist</h3><p class="note" style="padding-left:0">Descriptive only — this checklist does not provide investment advice.</p>'), "title and the exact descriptive line");
  eq((built.match(/Investment Checklist/g) || []).length, 1, "title appears once");
  eq((built.match(/<h4 /g) || []).length, 6, "six section panels"); eq((built.match(/<table>/g) || []).length, 6, "six tables");
  eq((built.match(/<th>/g) || []).length, 24, "four columns per table: Item, Value, Status, Note");
  ok(!/Research Checklist/.test(built), "does not reuse the Step 1 panel title");

  // ================= 3. a full stock: every row Available, values equal the Stock Research values =================
  const tcs = L.sections("TCS", D()), res = window.SLResearch.sections("TCS", D());
  const resVal = (t, l) => res.find((x) => x.title === t).items.find((i) => i.l === l).v;
  eq(row("TCS", "Business", "Company name"), { v: "TCS Ltd", st: "A", note: "" }, "company name Available");
  eq(st("TCS", "Business", "Sector"), "A", "sector Available");
  eq(row("TCS", "Fundamental Quality", "Revenue").v, resVal("Fundamental Quality", "Revenue"), "revenue value identical to Stock Research");
  eq(row("TCS", "Fundamental Quality", "Revenue").v, "2,50,000.5", "revenue shown as stored, Indian grouping");
  eq(row("TCS", "Fundamental Quality", "Total revenue growth").v, "+6.30%", "growth shown as stored");
  eq(row("TCS", "Fundamental Quality", "ROE").v, "51.20", "ROE value"); eq(row("TCS", "Fundamental Quality", "ROCE").v, "60.40", "ROCE value"); eq(row("TCS", "Fundamental Quality", "ROA").v, "20.10", "ROA value");
  ["Revenue", "Total revenue", "Total revenue growth", "Profit before tax", "Profit after tax", "Profit after tax growth", "ROE", "ROCE", "ROA"].forEach((l) => eq(st("TCS", "Fundamental Quality", l), "A", "TCS quality: " + l + " Available"));
  ["Total assets", "Total liabilities", "Total equity", "Liabilities / Equity"].forEach((l) => eq(st("TCS", "Financial Health", l), "A", "TCS health: " + l + " Available"));
  ["Operating cash flow", "Investing cash flow", "Financing cash flow"].forEach((l) => eq(st("TCS", "Cash Flow", l), "A", "TCS cash flow: " + l + " Available"));
  ["P/E", "P/B", "EV/EBITDA"].forEach((l) => eq(st("TCS", "Valuation", l), "A", "TCS valuation: " + l + " Available"));
  tcs.find((x) => x.title === "Technical").rows.forEach((r) => eq(r.st, "A", "TCS-like full history: technical row Available: " + r.l));
  eq(row("TCS", "Technical", "Last close").v, resVal("Technical Summary", "Last close (daily data)"), "technical values come straight from the Stock Research values");

  // ================= 4. Business =================
  eq(row("TCS", "Business", "Business description"), { v: "Unavailable", st: "U", note: "no business description exists in the current data files" }, "business description: always Unavailable, shows the word Unavailable");
  eq(row("MISS", "Business", "Company name"), { v: "-", st: "U", note: "not in the current data" }, "missing company name: '-' and Unavailable");
  eq(row("MISS", "Business", "Sector"), { v: "-", st: "U", note: "not in the current data" }, "missing sector: '-' and Unavailable");
  eq(st("NOSUCH", "Business", "Company name"), "U", "stock in no file: company name Unavailable"); eq(st("NOSUCH", "Business", "Business description"), "U", "stock in no file: description Unavailable");
  eq(row("HDFCBANK", "Business", "Sector"), { v: "Financial Services", st: "A", note: "" }, "a bank-like sector is just a sector: no inference is made from sector text");
  eq(st("HDFCBANK", "Financial Health", "Liabilities / Equity"), "A", "bank-like sector: Liabilities / Equity is NOT flagged");
  eq(row("HDFCBANK", "Financial Health", "Liabilities / Equity").note, "", "bank-like sector: no note either");

  // ================= 5. Fundamental Quality =================
  ["Revenue", "Total revenue", "Total revenue growth", "Profit before tax", "Profit after tax", "Profit after tax growth"].forEach((l) => eq(row("MISS", "Fundamental Quality", l), { v: "-", st: "U", note: "not in the current data" }, "MISS: " + l + " '-' Unavailable"));
  ["ROE", "ROCE", "ROA"].forEach((l) => eq(row("MISS", "Fundamental Quality", l), { v: "-", st: "U", note: "not in the current data" }, "MISS: " + l + " '-' Unavailable"));
  eq(row("BADNUM", "Fundamental Quality", "ROE").st, "U", "NaN ROE is not data: Unavailable"); eq(row("BADNUM", "Valuation", "P/E"), { v: "-", st: "U", note: "not in the current data" }, "text P/E is not data: '-' Unavailable");
  ["Revenue", "Total revenue", "Total revenue growth", "Profit before tax", "Profit after tax", "Profit after tax growth"].forEach((l) => { eq(st("MIXED", "Fundamental Quality", l), "R", "mixed basis: " + l + " Needs review"); });
  eq(row("MIXED", "Fundamental Quality", "Revenue").note, "statement basis is mixed (consolidated and standalone)", "mixed basis note");
  ["ROE", "ROCE", "ROA"].forEach((l) => eq(st("MIXED", "Fundamental Quality", l), "A", "mixed basis does not touch the fundamentals ratios: " + l));
  eq(row("MIXED", "Fundamental Quality", "Revenue").v, "2,50,000.5", "Needs review still shows the stored value");
  eq(st("TCS", "Fundamental Quality", "Revenue"), "A", "consolidated basis: Available");

  // ================= 6. Financial Health =================
  eq(row("EQNEG", "Financial Health", "Total equity"), { v: "-500", st: "R", note: "total equity is zero or negative" }, "negative equity: Needs review, value as stored");
  eq(row("EQNEG", "Financial Health", "Liabilities / Equity").st, "R", "negative equity: Liabilities / Equity Needs review"); eq(row("EQNEG", "Financial Health", "Liabilities / Equity").note, "total equity is zero or negative, so the ratio is not meaningful", "ratio note");
  eq(st("EQZERO", "Financial Health", "Total equity"), "R", "zero equity: Needs review"); eq(st("EQZERO", "Financial Health", "Liabilities / Equity"), "R", "zero equity: ratio Needs review");
  eq(row("EQMIS", "Financial Health", "Total equity"), { v: "700", st: "R", note: "total equity differs from total assets minus total liabilities" }, "equity 700 vs 1000-400=600: Needs review");
  eq(st("EQMIS", "Financial Health", "Liabilities / Equity"), "A", "ratio itself is fine when equity is positive and consistent with its own row");
  eq(st("NEARZERO", "Financial Health", "Total equity"), "A", "difference 0.005 is within the 0.01 tolerance: Available"); eq(st("EQEXACT", "Financial Health", "Total equity"), "R", "difference 0.02 is above the tolerance: Needs review");
  eq(st("MIXED", "Financial Health", "Total assets"), "R", "mixed basis: total assets Needs review"); eq(st("MIXED", "Financial Health", "Total liabilities"), "R", "mixed basis: total liabilities Needs review");
  eq(st("MIXED", "Financial Health", "Total equity"), "R", "mixed basis: total equity Needs review"); eq(row("MIXED", "Financial Health", "Total equity").note, "statement basis is mixed (consolidated and standalone)", "mixed note on equity");
  eq(st("MIXED", "Financial Health", "Liabilities / Equity"), "R", "mixed basis: ratio Needs review");
  eq(row("LEMISS", "Financial Health", "Liabilities / Equity"), { v: "-", st: "U", note: "not in the current data" }, "missing ratio: '-' Unavailable");
  ["Total assets", "Total liabilities", "Total equity", "Liabilities / Equity"].forEach((l) => eq(row("MISS", "Financial Health", l), { v: "-", st: "U", note: "not in the current data" }, "MISS health: " + l));
  // Debt: never shown, never inferred
  eq(row("TCS", "Financial Health", "Debt / Equity"), { v: "N/A", st: "U", note: "no reliable debt data in the current data files" }, "Debt / Equity: N/A and Unavailable");
  eq(row("TCS", "Financial Health", "Total debt"), { v: "N/A", st: "U", note: "no reliable debt data in the current data files" }, "Total debt: N/A and Unavailable");
  eq(row("TCS", "Financial Health", "Total liabilities").v, "60,000", "total liabilities is present for TCS ...");
  eq(row("TCS", "Financial Health", "Debt / Equity").v, "N/A", "... and still nothing is inferred from it as debt");
  eq(row("DEBT", "Financial Health", "Debt / Equity"), { v: "N/A", st: "R", note: "a stored value exists but is not shown until debt data is validated" }, "stored debt_to_equity 0.8: Needs review, still N/A, value NOT shown");
  eq(row("DEBT", "Financial Health", "Total debt"), { v: "N/A", st: "R", note: "a stored value exists but is not shown until debt data is validated" }, "stored total_debt 500: Needs review, still N/A");
  const debtHtml = L.build("DEBT", D()); no(debtHtml, />0\.80?</, "stored debt_to_equity 0.8 never appears as a value"); no(debtHtml, />500</, "stored total_debt 500 never appears as a value");
  eq(row("NOSUCH", "Financial Health", "Debt / Equity").v, "N/A", "no data at all: Debt / Equity is still N/A (structural)");

  // ================= 7. Cash Flow =================
  eq(row("TCS", "Cash Flow", "Operating cash flow"), { v: "55,000", st: "A", note: "" }, "operating cash flow"); eq(row("TCS", "Cash Flow", "Investing cash flow").v, "-20,000", "investing cash flow, negative as stored"); eq(row("TCS", "Cash Flow", "Financing cash flow").v, "-30,000", "financing cash flow");
  eq(row("TCS", "Cash Flow", "Free cash flow"), { v: "N/A", st: "U", note: "no unambiguous capital-expenditure line" }, "free cash flow absent: N/A and Unavailable (never operating minus investing)");
  no(L.build("TCS", D()), /35,000/, "operating 55000 + investing -20000 = 35000 is never shown as free cash flow");
  eq(row("FCFV", "Cash Flow", "Free cash flow"), { v: "1,234.5", st: "A", note: "" }, "stored free cash flow: Available, shown as stored, not recalculated (100-40 would be 60)");
  ["Operating cash flow", "Investing cash flow", "Financing cash flow"].forEach((l) => eq(row("MISS", "Cash Flow", l), { v: "-", st: "U", note: "not in the current data" }, "MISS cash flow: " + l));
  ["Operating cash flow", "Investing cash flow", "Financing cash flow"].forEach((l) => eq(st("MIXED", "Cash Flow", l), "R", "mixed basis: " + l + " Needs review"));
  eq(st("MIXED", "Cash Flow", "Free cash flow"), "U", "free cash flow has no Needs review rule: absent stays Unavailable");

  // ================= 8. Valuation =================
  eq(row("TCS", "Valuation", "P/E"), { v: "25.50", st: "A", note: "" }, "P/E"); eq(row("TCS", "Valuation", "P/B").v, "12.30", "P/B"); eq(row("TCS", "Valuation", "EV/EBITDA").v, "18.70", "EV/EBITDA");
  eq(row("NEGVAL", "Valuation", "P/E"), { v: "-5.00", st: "R", note: "value is zero or negative" }, "negative P/E: Needs review, value as stored");
  eq(row("NEGVAL", "Valuation", "P/B"), { v: "0.00", st: "R", note: "value is zero or negative" }, "zero P/B: Needs review"); eq(st("NEGVAL", "Valuation", "EV/EBITDA"), "A", "positive EV/EBITDA: Available");
  ["P/E", "P/B", "EV/EBITDA"].forEach((l) => eq(row("MISS", "Valuation", l), { v: "-", st: "U", note: "not in the current data" }, "MISS valuation: " + l));

  // ================= 9. Technical: Unavailable vs Data insufficient, boundary by boundary =================
  const T = (sym, label, d) => st(sym, "Technical", label, d);
  eq(T("NOSUCH", "Last close"), "U", "stock absent from historical.json: Unavailable");
  const none = D({ hist: null });
  eq(row("TCS", "Technical", "Last close", none), { v: "-", st: "U", note: "historical data is not loaded" }, "no historical.json: '-' Unavailable, with the reason");
  tcs.find((x) => x.title === "Technical").rows.forEach((r) => eq(row("TCS", "Technical", r.l, none).st, "U", "no historical.json: " + r.l + " Unavailable"));
  eq(row("NOSUCH", "Technical", "RSI(14)").note, "no daily candles for this stock in historical.json", "stock absent: reason");
  eq(T("N1", "Last close"), "A", "1 candle: last close Available"); eq(T("N1", "Return 1M"), "D", "1 candle: 1M return Data insufficient");
  eq(row("N1", "Technical", "Return 1M"), { v: "-", st: "D", note: "1 daily candle available, not enough for this value" }, "1 candle: '-' Data insufficient, singular wording");
  eq(row("N19", "Technical", "RSI(14)").note, "19 daily candles available, not enough for this value", "19 candles: plural wording");
  // DMA boundaries (value comes from the existing SLTech.compute)
  eq(T("N19", "20 DMA (close vs average)"), "D", "19 candles: 20 DMA Data insufficient"); eq(T("N20", "20 DMA (close vs average)"), "A", "20 candles: 20 DMA Available");
  eq(T("N49", "50 DMA (close vs average)"), "D", "49 candles: 50 DMA Data insufficient"); eq(T("N50", "50 DMA (close vs average)"), "A", "50 candles: 50 DMA Available");
  eq(T("N199", "200 DMA (close vs average)"), "D", "199 candles: 200 DMA Data insufficient"); eq(T("N200", "200 DMA (close vs average)"), "A", "200 candles: 200 DMA Available");
  eq(T("N49", "20 DMA (close vs average)"), "A", "49 candles: 20 DMA is still Available"); eq(T("N199", "50 DMA (close vs average)"), "A", "199 candles: 50 DMA is still Available");
  // returns: history must reach back to the calendar cutoff (daily candles from 2025-01-01)
  eq(T("N29", "Return 1M"), "D", "29 days of history: 1M return Data insufficient"); eq(T("N49", "Return 1M"), "A", "49 days of history: 1M return Available");
  eq(T("N49", "Return 3M"), "D", "49 days: 3M return Data insufficient"); eq(T("N199", "Return 3M"), "A", "199 days: 3M return Available");
  eq(T("N199", "Return 6M"), "A", "199 days: 6M return Available (cutoff 2025-01-01 reached)"); eq(T("N199", "Return 1Y"), "D", "199 days: 1Y return Data insufficient"); eq(T("N400", "Return 1Y"), "A", "400 days: 1Y return Available");
  // RSI / MACD / Relative Strength follow the existing chart calculation (it needs 30 candles at all; the 9-day EMA line and histogram need 34)
  ["RSI(14)", "MACD line", "MACD 9-day EMA line", "MACD histogram", "Relative Strength vs Nifty 50 (1Y)"].forEach((l) => eq(T("N29", l), "D", "29 candles: " + l + " Data insufficient"));
  eq(T("N30", "RSI(14)"), "A", "30 candles: RSI Available"); eq(T("N30", "MACD line"), "A", "30 candles: MACD line Available"); eq(T("N30", "Relative Strength vs Nifty 50 (1Y)"), "A", "30 candles: Relative Strength Available");
  eq(T("N30", "MACD 9-day EMA line"), "D", "30 candles: 9-day EMA line Data insufficient"); eq(T("N30", "MACD histogram"), "D", "30 candles: histogram Data insufficient");
  eq(T("N33", "MACD 9-day EMA line"), "D", "33 candles: 9-day EMA line Data insufficient"); eq(T("N34", "MACD 9-day EMA line"), "A", "34 candles: 9-day EMA line Available"); eq(T("N34", "MACD histogram"), "A", "34 candles: histogram Available");
  // values really are the existing calculations
  const hist400 = S.prepare(HIST, "N400", "1Y");
  eq(row("N400", "Technical", "RSI(14)").v, hist400.rsi[hist400.rsi.length - 1].value.toFixed(2), "RSI equals SLChart.prepare() output");
  eq(row("N400", "Technical", "MACD line").v, hist400.macd.line[hist400.macd.line.length - 1].value.toFixed(2), "MACD equals SLChart.prepare() output");
  eq(row("N400", "Technical", "Relative Strength vs Nifty 50 (1Y)").v, hist400.rs.last.toFixed(2), "Relative Strength equals SLChart.prepare() (fixed 1Y window)");
  eq(row("N400", "Technical", "Last close").v, "₹499.00", "last close from SLTech.compute()");
  // benchmark rules
  const noBench = D({ hist: Object.assign({}, HIST, { benchmark: undefined }) });
  eq(row("N400", "Technical", "Relative Strength vs Nifty 50 (1Y)", noBench), { v: "-", st: "U", note: "Nifty 50 benchmark candles are not in historical.json" }, "no benchmark: Relative Strength Unavailable");
  eq(T("N400", "RSI(14)", noBench), "A", "no benchmark: RSI is still Available");
  const farBench = D({ hist: Object.assign({}, HIST, { benchmark: { candles: bench(400).map((c, i) => Object.assign(c, { date: day("2020-01-01", i) })) } }) });
  eq(row("N400", "Technical", "Relative Strength vs Nifty 50 (1Y)", farBench), { v: "-", st: "D", note: "not enough overlapping daily candles for this value" }, "benchmark on other dates: Data insufficient");
  eq(T("N400", "Last close", { val: FUND, fin: FIN, hist: { stocks: { N400: { candles: [{ date: "bad", open: 1, high: 1, low: 1, close: 1 }] } } } }), "U", "only invalid candles: Unavailable (cleaned out by the existing rule)");
  eq(T("N400", "RSI(14)", { val: FUND, fin: FIN, hist: { stocks: "x" } }), "U", "malformed historical.json: Unavailable");

  // ================= 10. missing files and statuses are only the four words =================
  const empty = D({ val: null, fin: null, hist: null });
  L.sections("TCS", empty).forEach((sec) => sec.rows.forEach((r) => ok(["U"].includes(r.st) || r.st === "U", "no files at all: " + r.l + " Unavailable")));
  const words = new Set(); ["TCS", "EQNEG", "DEBT", "NEGVAL", "N1", "N30", "MISS", "NOSUCH"].forEach((s) => L.sections(s, D()).forEach((sec) => sec.rows.forEach((r) => words.add(r.st))));
  eq([...words].sort(), ["A", "D", "R", "U"], "only the four status codes ever occur");
  const shown = new Set(); [...built.matchAll(/<td>(Available|Unavailable|Needs review|Data insufficient)<\/td>/g)].forEach((m) => shown.add(m[1]));
  ok(shown.has("Available") && shown.has("Unavailable"), "status words render in the table");
  eq([...L.build("EQNEG", D()).matchAll(/<td>(Needs review)<\/td>/g)].length > 0, true, "Needs review renders as the words 'Needs review'");
  ok(/<td>Data insufficient<\/td>/.test(L.build("N1", D())), "Data insufficient renders as the words 'Data insufficient'");

  // ================= 11. stub page: the real scripts on a stub DOM with a MutationObserver =================
  const stubLib = (log) => ({ createChart(el, opts) { const ch = { el, opts, series: [], removed: false, addCandlestickSeries() { const s = { data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; ch.series.push(s); return s; },
    addLineSeries() { const s = { data: [], lines: [], setData(d) { s.data = d; }, createPriceLine(o) { s.lines.push(o); } }; ch.series.push(s); return s; }, addHistogramSeries() { const s = { data: [], lines: [], setData(d) { s.data = d; }, createPriceLine() {} }; ch.series.push(s); return s; },
    timeScale() { return { fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }; }, remove() { ch.removed = true; } }; log.push(ch); return ch; } });
  const FILES = () => ({ "fundamentals.json": FUND, "financials.json": FIN, "historical.json": HIST });
  async function boot(hash, files = FILES(), { delay = 0, observer = true, settle = null, research = true } = {}) {
    const els = {}, win = {}, charts = [], fetched = [], observers = [];
    let boxHtml = "", children = [];
    const sched = () => queueMicrotask(() => observers.forEach((f) => f([])));
    const box = { id: "detail", get innerHTML() { return boxHtml + children.map((c) => c.innerHTML).join(""); },
      set innerHTML(v) { boxHtml = v; children.forEach((c) => { c.parentNode = null; }); children = []; sched(); },
      appendChild(c) { c.parentNode = box; children.push(c); sched(); return c; }, setAttribute() {}, addEventListener() {}, querySelector: () => null };
    const mk = (id) => ({ id, innerHTML: "", textContent: "", attrs: {}, listeners: {}, setAttribute() {}, addEventListener() {}, querySelector: () => null });
    if (observer) global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) observers.push(cb); }; }; else delete global.MutationObserver;
    global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {}, LightweightCharts: stubLib(charts) };
    global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => (i === "detail" ? box : els[i] || (els[i] = mk(i))),
      addEventListener() {}, querySelector: () => null, createElement: () => ({ parentNode: null, innerHTML: "", id: "" }), head: { appendChild() {} } };
    global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); const f = files[u.split("/").pop()] ?? (u.endsWith("scans.json") ? { as_of: "2026-10-01", source: "NSE", stocks: [] } : null); return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
    eval(detailCode); eval(chartCode); eval(techCode); if (research) eval(researchCode); eval(checkCode);
    await sleep(settle ?? 80 + delay * 3);
    const nav = async (h, ms = 80 + delay * 3) => { window.location.hash = h; win.hashchange(); await sleep(ms); };
    const byId = (id) => children.filter((c) => c.id === id);
    return { els, charts, fetched, nav, win, children: () => children, ids: () => children.map((c) => c.id), byId, check: () => byId("detailChecklist")[0], research: () => byId("detailResearch")[0],
      detailHtml: () => boxHtml };
  }
  const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
  const cell = (h, label) => { const m = h.match(new RegExp("<tr><td>" + label.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&") + '</td><td class="n">([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td></tr>')); return m ? { v: m[1], st: m[2], note: m[3] } : null; };

  let t = await boot("#stock=TCS");
  eq(t.ids(), ["detailResearch", "detailChecklist"], "placement: Stock Research first, then the Investment Checklist, both appended to #detail");
  eq(t.byId("detailChecklist").length, 1, "exactly one checklist section"); eq(t.byId("detailResearch").length, 1, "Step 1 still places exactly one research section");
  ok(t.detailHtml().includes('id="detailChart"') && t.detailHtml().includes("Stock Detail: TCS"), "the Stock Detail view is intact above both sections");
  ok(t.research().innerHTML.includes("<h4 style=\"margin:0 0 .2em\">Research Checklist</h4>"), "Step 1 Research Checklist panel is still rendered unchanged");
  ok(t.check().innerHTML.includes("Investment Checklist") && t.check().innerHTML.includes("Descriptive only — this checklist does not provide investment advice."), "the checklist section shows its title and descriptive line");
  eq(t.fetched.filter((u) => u.includes("fundamentals")).length, 3, "fundamentals.json: one fetch each by Stock Detail, Stock Research and the checklist (the checklist adds exactly one)");
  eq(t.fetched.filter((u) => u.includes("historical")).length, 4, "historical.json: chart, snapshot, research and the checklist each fetch it once");
  eq([...new Set(t.fetched.map((u) => u.replace(/\?.*$/, "")))].sort(), ["out/financials.json", "out/fundamentals.json", "out/historical.json", "out/scans.json"].filter((u) => t.fetched.some((f) => f.startsWith(u))).sort(), "no URL beyond the existing data files is requested");
  ok(t.fetched.every((u) => /^out\/[a-z_]+\.json$/.test(u)), "every request is a relative out/*.json file");
  const page = t.check().innerHTML;
  eq(cell(page, "Revenue"), { v: "2,50,000.5", st: "Available", note: "" }, "rendered row: value, status word, empty note");
  eq(cell(page, "Debt / Equity"), { v: "N/A", st: "Unavailable", note: "no reliable debt data in the current data files" }, "rendered Debt / Equity row");
  eq(cell(page, "Free cash flow"), { v: "N/A", st: "Unavailable", note: "no unambiguous capital-expenditure line" }, "rendered Free cash flow row");
  eq(cell(page, "Business description").v, "Unavailable", "rendered description row");
  eq((page.match(/<tr><td>/g) || []).length, 3 + 9 + 6 + 4 + 3 + 13, "38 rows rendered");

  // missing values render as '-', structural as 'N/A'
  t = await boot("#stock=MISS"); const mp = t.check().innerHTML;
  eq(cell(mp, "Company name"), { v: "-", st: "Unavailable", note: "not in the current data" }, "rendered missing value is '-'");
  eq(cell(mp, "Total debt").v, "N/A", "rendered structural gap is N/A"); eq(cell(mp, "P/E").v, "-", "rendered missing P/E is '-'");
  eq(cell(mp, "Return 1Y").st, "Unavailable", "MISS has no candles in the file: technical rows Unavailable");

  // statuses on the rendered page for a Needs review / Data insufficient stock
  t = await boot("#stock=EQNEG"); eq(cell(t.check().innerHTML, "Total equity").st, "Needs review", "rendered Needs review");
  t = await boot("#stock=N30"); eq(cell(t.check().innerHTML, "MACD histogram").st, "Data insufficient", "rendered Data insufficient");

  // routing
  t = await boot(""); eq(t.children().length, 0, "dashboard: no checklist"); ok(!t.fetched.some((u) => u.includes("financials")), "dashboard: nothing fetched for the checklist");
  t = await boot("#stock=TCS"); await t.nav("#stock=EQNEG"); eq(t.ids(), ["detailResearch", "detailChecklist"], "navigating to another stock: still one of each, in order");
  ok(cell(t.check().innerHTML, "Total equity") && cell(t.check().innerHTML, "Total equity").st === "Needs review", "after navigation the checklist shows the new stock, not the old one");
  await t.nav("#stock=TCS"); eq(cell(t.check().innerHTML, "Total equity").st, "Available", "and back again");
  await t.nav(""); eq(t.ids(), ["detailResearch", "detailChecklist"], "back to the dashboard: nothing is added or duplicated (the hidden detail view is left as the Stock Detail script leaves it)");
  t = await boot("#stock=TCS", FILES(), { delay: 40, settle: 40 }); eq(t.ids(), ["detailResearch", "detailChecklist"], "before the data arrives: both placeholders are placed, research first"); ok(/Loading/.test(t.check().innerHTML) && !/<table>/.test(t.check().innerHTML), "before the data arrives: only the title and a Loading line, no rows");
  await sleep(500); ok(/<table>/.test(t.check().innerHTML) && !/Loading/.test(t.check().innerHTML), "after the data arrives: the rows replace the placeholder"); eq(t.ids(), ["detailResearch", "detailChecklist"], "order unchanged after painting");
  t = await boot("#stock=TCS", FILES(), { delay: 120, settle: 100 }); await t.nav("#stock=EQNEG", 20); await t.nav("", 20); await sleep(700);
  ok(t.check() && /Loading/.test(t.check().innerHTML) && !/<table>/.test(t.check().innerHTML), "slow files and the user left: the hidden placeholder is never painted with data");
  t = await boot("#stock=TCS", FILES(), { delay: 80, settle: 60 }); await t.nav("#stock=EQNEG", 20); await sleep(700);
  eq(t.byId("detailChecklist").length, 1, "slow files, quick navigation: one checklist"); eq(cell(t.check().innerHTML, "Total equity").st, "Needs review", "slow files, quick navigation: it shows the stock currently in the hash");
  t = await boot("#stock=TCS", FILES(), { research: false }); eq(t.children().length, 0, "without the Stock Research section the checklist is not placed (it always follows Stock Research)");
  t = await boot("#stock=TCS", FILES(), { observer: false }); eq(t.byId("detailChecklist").length, 0, "no MutationObserver: nothing is appended (Stock Detail stays as it is)");
  t = await boot("#stock=TCS", { "fundamentals.json": null, "financials.json": null, "historical.json": null }); ok(t.check() && /Unavailable/.test(t.check().innerHTML), "all three files missing: the checklist still renders, every row Unavailable");
  eq(cell(t.check().innerHTML, "Revenue").st, "Unavailable", "all files missing: revenue Unavailable");
  t = await boot("#stock=NAMEXSS"); ok(t.check().innerHTML.includes("Infosys &amp; Co &lt;Ltd&gt;") && !t.check().innerHTML.includes("<Ltd>"), "a hostile company name is escaped");

  // ================= 12. wording: descriptive only =================
  const BANNED = /\b(buy|sell|strong|bullish|bearish|signals?|ratings?|scores?|target|undervalued|overvalued|recommend\w*|overall|outlook|verdict|favou?rable)\b/i;
  const codeNoComments = checkCode.replace(/\/\*[^]*?\*\//g, "");
  no(codeNoComments, BANNED, "the checklist code has none of the banned words");
  no(checkCode.replace(/\/\*[^]*?\*\//g, "").replace(/mc\.signal/g, ""), /\bsignal/i, "no 'signal' anywhere (the property name is never even read)");
  ["TCS", "EQNEG", "DEBT", "NEGVAL", "N1", "N30", "MISS", "MIXED", "FCFV", "HDFCBANK"].forEach((s) => no(text(L.build(s, D())), BANNED, "rendered text for " + s + " has none of the banned words"));
  no(text(L.build("TCS", D())), /\d+\s*(%|of)\s*\d*\s*(items|checks|rows)|\b\d+\s*\/\s*\d+\b/i, "no tally, ratio or composite figure in the rendered text");
  no(L.build("TCS", D()), /Available\s*[:=]\s*\d+|Unavailable\s*[:=]\s*\d+|Needs review\s*[:=]\s*\d+/, "no 'status: count' line");
  no(codeNoComments, /\.(length)\s*\/|filter\([^)]*\)\.length/, "the code never counts rows by status");
  ok(!/<b[^>]*>(Available|Unavailable|Needs review|Data insufficient)<\/b>/.test(L.build("TCS", D())), "status words are plain text, not badges that read as a result");

  // ================= 13. security and scope =================
  no(checkCode, /Authorization|Bearer|access_token|api\.upstox|upstox\.com|localStorage|sessionStorage|WebSocket|EventSource|setInterval|XMLHttpRequest|eval\(|new Function|document\.write/i, "no token, no Upstox, no storage, no streaming, no dynamic code");
  eq((checkCode.match(/fetch\(/g) || []).length, 1, "exactly one fetch call (a helper)");
  eq([...new Set(checkCode.match(/out\/[a-z_]+\.json/g))].sort(), ["out/financials.json", "out/fundamentals.json", "out/historical.json"], "it reads only the three existing data files");
  eq((checkCode.match(/\bcache:"no-store"/g) || []).length, 1, "the helper uses no-store like the other loaders");
  no(checkCode, /https?:\/\//, "no external URL in the block");
  no(checkCode, /debt_to_equity\s*\/|total_liabilities\s*[-\/*]|operating\s*[-+]\s*\S*investing|investing\s*[-+]/, "no debt inferred from liabilities and no operating-minus-investing arithmetic");
  no(checkCode.replace(/\/\*[^]*?\*\//g, ""), /\.toFixed|N2\.format|N0\.format/, "the checklist formats nothing itself: every value is the Stock Research string");
  re(checkCode, /esc\(r\.l\)/, "row labels are escaped"); re(checkCode, /esc\(r\.note\)/, "row notes are escaped"); re(checkCode, /esc\(sec\.title\)/, "section titles are escaped");
  ok(!/\.innerHTML\s*=\s*[^;]*\bd\.(val|fin|hist)\b/.test(checkCode), "raw data is never written to the page directly");
  eq(sha(checkCode), sha(blocks[5]), "the checklist block is block 6 (index 5)");

  console.log("Checklist tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
