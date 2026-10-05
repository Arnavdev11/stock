// Run: node tests/test_financial_history_compare.js   (no network, no browser; stubs the DOM, MutationObserver and fetch)
// Tests the Phase 5B stock comparison: the 2-5 stock rule, hash routing (#compare=..., #compare-pick[=SYM]), the cap of 5 with a notice, the picker, the side-by-side
// overview and financial-history tables (fiscal years, change, CAGR, banks vs other companies, ITC boundary, markers), "-" for gaps, unavailable states, neutral wording,
// the two entry points, the files read, and that every older piece of the page (seven classic blocks, Financial History module, everything else) is exactly what it was.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">';
const fhCode = (html.split(FHTAG)[1] || "").split("</script>")[0], cmpCode = (html.split(CMPTAG)[1] || "").split("</script>")[0];
assert.ok(fhCode.includes("Phase 4 Step 4E - Financial History") && cmpCode.includes("Phase 5B - Compare stocks"), "both module scripts exist");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();

// ---------- fixtures (tests only) ----------
const V = (o) => Object.assign({ revenue: null, total_revenue: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, operating_cash_flow: null }, o);
const R = (fy, values, { official = false, mismatch = false } = {}) => ({ fy, period: "Mar " + fy, basis: "consolidated", values: V(values),
  verification: { status: "verified", reconciliation: official ? "not_checked" : mismatch ? "mismatch" : "matched" }, source: { provider: official ? "Official annual report" : "Upstox" } });
const yrs = (sym, bank, fy0, n, start, o = {}) => Array.from({ length: n }, (_, i) => { const v = +(start * Math.pow(1.1, i)).toFixed(4);
  return R(fy0 + i, { [bank ? "total_revenue" : "revenue"]: v, profit_before_tax: v / 2, profit_after_tax: v / 4, eps_basic: 10 + i, operating_cash_flow: v / 3 }, o); });
const STK = (sym, bank, years) => ({ symbol: sym, statement_layout: bank ? "financial" : "standard", years });
const HDOC = () => ({ schema: 1, as_of: "2026-10-04", source: "Upstox (API) and official company annual reports", stocks: {
  TCS: STK("TCS", false, [R(2022, { revenue: 100, profit_before_tax: 50, profit_after_tax: 25, eps_basic: 10, operating_cash_flow: 33 }, { official: true }), ...yrs("TCS", false, 2023, 4, 110)]),
  ITC: STK("ITC", false, [R(2022, { revenue: 50, profit_before_tax: 20, profit_after_tax: 15, eps_basic: 12 }, { official: true }), ...yrs("ITC", false, 2023, 4, 100, { mismatch: true })]),
  INFY: STK("INFY", false, yrs("INFY", false, 2023, 4, 200)), RELIANCE: STK("RELIANCE", false, yrs("RELIANCE", false, 2023, 4, 900)), LT: STK("LT", false, yrs("LT", false, 2023, 4, 300)), MARUTI: STK("MARUTI", false, yrs("MARUTI", false, 2023, 4, 80)),
  HDFCBANK: STK("HDFCBANK", true, yrs("HDFCBANK", true, 2023, 4, 1000)), ICICIBANK: STK("ICICIBANK", true, yrs("ICICIBANK", true, 2023, 4, 700)), SBIN: STK("SBIN", true, yrs("SBIN", true, 2023, 4, 600)),
  GAPPY: STK("GAPPY", false, [R(2023, { revenue: 10 }), R(2024, { revenue: 11 }), R(2026, { revenue: 14 })]),
  NEGLAST: STK("NEGLAST", false, [R(2025, { revenue: 10 }), R(2026, { revenue: -4 })]) } });
const SYMS = ["TCS", "ITC", "INFY", "RELIANCE", "LT", "MARUTI", "HDFCBANK", "ICICIBANK", "SBIN", "GAPPY", "NEGLAST", "NOHIST"];
const FUND = () => ({ as_of: "2026-10-01", source: "Upstox", stocks: SYMS.map((s, i) => ({ symbol: s, company_name: s === "LT" ? "Larsen <b>&</b> Toubro" : s + " Ltd", sector: i % 2 ? "Banks" : "IT", pe: 20 + i, pb: 3 + i / 10, roa: 5 + i, roe: 15 + i, roce: 18 + i, ev_ebitda: s === "SBIN" ? null : 10 + i })) });
const cd = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const PRICES = () => ({ updated: "2026-10-05", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: [cd("2026-10-05", 25000), cd("2026-10-06", 25100)] }, stocks: {
  TCS: { symbol: "TCS", candles: [cd("2026-09-30", 3300), cd("2026-10-01", 3400), cd("2026-10-02", 3442.5)] },                          // +1.25% on the last close
  INFY: { symbol: "INFY", candles: [cd("2026-10-01", 1600), cd("2026-10-02", 1500)] },                                                 // -6.25%
  ITC: { symbol: "ITC", candles: [cd("2026-09-30", 400), cd("2026-10-01", 400)] },                                                     // flat
  LT: { symbol: "LT", candles: [cd("2026-10-02", 3600)] },                                                                             // one candle only
  RELIANCE: { symbol: "RELIANCE", candles: [] }, MARUTI: { symbol: "MARUTI", candles: [cd("2026-10-01", 0), cd("2026-10-02", 100)] },   // previous close 0
  HDFCBANK: { symbol: "HDFCBANK", candles: [cd("2026-10-01", 1700), { date: "2026-10-02", close: null }, { date: "bad", close: 1 }, null, { close: 5 }, cd("2026-10-03", 1700 * 1.1)] }, // junk candles are skipped
  SBIN: { symbol: "SBIN", candles: [cd("2026-10-03", 800), cd("2026-10-01", 780), cd("2026-10-02", 790)] },                            // out of file order: sorted by date
  ICICIBANK: { symbol: "ICICIBANK", candles: [cd("2026-10-01", 1000), cd("2026-10-02", 1000)] } } });
const D = (o = {}) => Object.assign({ val: FUND(), hist: HDOC(), prices: PRICES() }, o);

// ---------- load the two modules' pure APIs ----------
global.MutationObserver = undefined;
const mkEnv = () => { global.window = { location: { hash: "" }, addEventListener() {}, scrollTo() {} }; global.document = { getElementById: () => null, querySelector: () => null, body: { classList: { add() {}, remove() {} } } }; global.fetch = async () => ({ ok: false }); };
mkEnv(); (0, eval)(fhCode); const F = window.SLFinHistory; ok(F && typeof F.model === "function", "the Financial History module exposes model/cagr/change");
(0, eval)(cmpCode); const C = window.SLCompare; ok(C && typeof C.build === "function", "the compare module exposes its API");
const keep = { SLFinHistory: F }; const withF = () => { window.SLFinHistory = F; };

// ================= 1. hash parsing =================
const P = C.parseHash;
eq(P("#compare=TCS,ITC"), { mode: "compare", symbols: ["TCS", "ITC"], invalid: [] }, "two symbols");
eq(P("#compare=tcs, itc ,infy").symbols, ["TCS", "ITC", "INFY"], "case and spaces are normalised, order kept");
eq(P("#compare=TCS,TCS,tcs,ITC").symbols, ["TCS", "ITC"], "duplicates are dropped, first kept");
eq(P("#compare=TCS,,ITC,").symbols, ["TCS", "ITC"], "empty tokens are ignored");
eq(P("#compare=TCS%2CITC").symbols, ["TCS", "ITC"], "an encoded comma works");
eq(P("#compare=M%26M,BAJAJ-AUTO").symbols, ["M&M", "BAJAJ-AUTO"], "symbols with & and - are valid");
eq(P("#compare=<script>,12.5,TCS").invalid, ["<SCRIPT>", "12.5"], "invalid tokens are reported, never used"); eq(P("#compare=<script>,12.5,TCS").symbols, ["TCS"], "and dropped");
eq(P("#compare=").symbols, [], "empty list"); eq(P("#compare=").mode, "compare", "is still the compare route");
eq(P("#compare-pick"), { mode: "pick", symbols: [], invalid: [] }, "the picker route"); eq(P("#compare-pick=tcs").symbols, ["TCS"], "the picker with one stock preselected"); eq(P("#compare-pick=TCS").mode, "pick", "mode pick");
for (const h of ["", "#", "#stock=TCS", "#compare", "#comparex=TCS", "#compare=TCS&x=1", "#compare-pick=TCS&x=1", "#compare-picker", "#demo"]) eq(P(h).mode, null, "not a compare route: " + h);
eq(P("#compare=%E0%A4%A").mode, "compare", "a broken escape is still the compare route"); eq(P("#compare=%E0%A4%A").symbols, [], "with no symbols"); eq(P("#compare=%E0%A4%A").invalid, ["(unreadable)"], "and a notice token");
eq(P("#compare=TCS#x").invalid, ["TCS#X"], "a stray # inside the list makes that token invalid"); eq(P(null).mode, null, "null hash"); eq(P(undefined).mode, null, "undefined hash");
eq(C.MIN, 2, "minimum is 2"); eq(C.MAX, 5, "maximum is 5");

// ================= 2. resolve (known symbols, cap of 5) =================
const known = (arr) => Object.fromEntries(arr.map((s) => [s, 1])), K = known(SYMS);
eq(C.resolve(["TCS", "ITC"], K), { symbols: ["TCS", "ITC"], unknown: [], extra: [] }, "two known symbols");
eq(C.resolve(["TCS", "ITC", "INFY", "LT", "SBIN"], K).symbols.length, 5, "exactly five are kept"); eq(C.resolve(["TCS", "ITC", "INFY", "LT", "SBIN"], K).extra, [], "with nothing extra");
eq(C.resolve(["TCS", "ITC", "INFY", "LT", "SBIN", "MARUTI", "RELIANCE"], K), { symbols: ["TCS", "ITC", "INFY", "LT", "SBIN"], unknown: [], extra: ["MARUTI", "RELIANCE"] }, "more than five: the first five are kept, the rest reported");
eq(C.resolve(["NOPE", "TCS", "ITC"], K), { symbols: ["TCS", "ITC"], unknown: ["NOPE"], extra: [] }, "unknown symbols are reported and dropped");
eq(C.resolve(["NOPE", "TCS", "ITC", "INFY", "LT", "SBIN", "MARUTI"], K), { symbols: ["TCS", "ITC", "INFY", "LT", "SBIN"], unknown: ["NOPE"], extra: ["MARUTI"] }, "unknown ones do not use up the five");
eq(C.resolve(["TCS"], {}), { symbols: [], unknown: ["TCS"], extra: [] }, "nothing known");
eq(C.resolve([], K).symbols, [], "empty");

// ================= 3. selection rules =================
eq(C.toggle([], "TCS", true), ["TCS"], "add one"); eq(C.toggle(["TCS"], "ITC", true), ["TCS", "ITC"], "add keeps order"); eq(C.toggle(["TCS"], "TCS", true), ["TCS"], "adding twice does nothing");
eq(C.toggle(["TCS", "ITC"], "TCS", false), ["ITC"], "remove"); eq(C.toggle(["ITC"], "TCS", false), ["ITC"], "removing an absent one does nothing");
const five = ["TCS", "ITC", "INFY", "LT", "SBIN"]; eq(C.toggle(five, "MARUTI", true), five, "a sixth cannot be added"); eq(C.toggle(five, "SBIN", false), five.slice(0, 4), "but one can be removed");
const inp = ["A"]; C.toggle(inp, "B", true); eq(inp, ["A"], "toggle never mutates its input");
for (const [n, want] of [[0, false], [1, false], [2, true], [3, true], [4, true], [5, true], [6, false]]) eq(C.canApply(Array.from({ length: n }, (_, i) => "S" + i)), want, "canApply with " + n);
eq(C.hashFor(["TCS"]), null, "no one-stock URL is ever made"); eq(C.hashFor([]), null, "no empty URL"); eq(C.hashFor(["TCS", "ITC"]), "#compare=TCS,ITC", "two"); eq(C.hashFor(five), "#compare=TCS,ITC,INFY,LT,SBIN", "five"); eq(C.hashFor(five.concat("MARUTI")), null, "six: no URL");
eq(C.hashFor(["M&M", "TCS"]), "#compare=M%26M,TCS", "symbols are encoded"); eq(P(C.hashFor(["M&M", "TCS"])).symbols, ["M&M", "TCS"], "and round-trip");

// ================= 4. the rendered view =================
withF();
const heads = (h, tbl) => { const t = h.match(new RegExp('<table data-ctable="' + tbl + '">(.*?)</table>', "s")); return t ? [...t[1].match(/<thead>(.*?)<\/thead>/s)[1].matchAll(/<th scope="col">(.*?)<\/th>/g)].map((x) => text(x[1])).slice(1) : null; };
const row = (h, tbl, attr, val) => { const t = h.match(new RegExp('<table data-ctable="' + tbl + '">(.*?)</table>', "s")); if (!t) return null; const m = t[1].match(new RegExp('<tr data-' + attr + '="' + val + '">(.*?)</tr>', "s")); return m ? [...m[1].matchAll(/<t[dh][^>]*>(.*?)<\/t[dh]>/gs)].map((x) => text(x[1])) : null; };
const tables = (h) => [...h.matchAll(/<table data-ctable="([a-z_]+)"/g)].map((x) => x[1]);
let h = C.build("#compare=TCS,INFY", D());
eq(tables(h), ["overview", "revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "overview plus five history tables");
eq(heads(h, "overview"), ["TCS", "INFY"], "columns are the two stocks, in the order given"); eq(heads(h, "revenue"), ["TCS", "INFY"], "and in every table");
eq(row(h, "overview", "crow", "company"), ["Company", "TCS Ltd", "INFY Ltd"], "company names"); eq(row(h, "overview", "crow", "pe"), ["P/E", "20.00", "22.00"], "P/E"); eq(row(h, "overview", "crow", "roce"), ["ROCE %", "18.00", "20.00"], "ROCE");
eq(row(h, "overview", "crow", "ltp"), ["LTP (last close, ₹)", "3,442.50", "1,500.00"], "LTP is the close of the latest daily candle"); eq(row(h, "overview", "crow", "change"), ["Change vs previous close", "+1.25%", "-6.25%"], "change is the latest close against the immediately preceding candle's close");
eq(text(h.match(/<th scope="row">LTP[^<]*<\/th>/)[0]), "LTP (last close, ₹)", "the LTP row label is exactly this"); eq(text(h.match(/<th scope="row">Change[^<]*<\/th>/)[0]), "Change vs previous close", "the change row label is exactly this"); no(h, /Today's change/, "the old label is gone");
re(text(h), /LTP and change are the last daily close in the historical data, as of 2026-10-02 \(not a live price\)\./, "the data note states the candle date once when the stocks agree");
eq(row(h, "overview", "crow", "basis"), ["Statements basis", "consolidated", "consolidated"], "basis shown");
eq(row(h, "revenue", "fy", "2022"), ["FY2022", "100 AR", "-"], "TCS has FY2022 (official annual report marker), INFY has no FY2022: a dash"); eq(row(h, "revenue", "fy", "2023"), ["FY2023", "110", "200"], "FY2023");
eq(row(h, "revenue", "fy", "2026"), ["FY2026", "146.41", "266.2"], "FY2026"); eq(row(h, "eps_basic", "fy", "2024"), ["FY2024", "11.00", "11.00"], "EPS to two decimals");
eq(row(h, "profit_after_tax", "fy", "2026"), ["FY2026", "36.6 AR".replace(" AR", ""), "66.55"], "PAT");
const m1 = F.model(HDOC(), "TCS"), m2 = F.model(HDOC(), "INFY");
const pcs = (v) => (v === null ? "-" : (v > 0 ? "+" : "") + v.toFixed(2) + "%");
eq(row(h, "revenue", "cchg", "revenue")[1], pcs(F.change(m1, 4, "revenue")) + " FY26 vs FY25", "change is the Financial History module's own figure (latest vs previous year)"); eq(row(h, "revenue", "cchg", "revenue")[2], pcs(F.change(m2, 3, "revenue")) + " FY26 vs FY25", "INFY too");
eq(row(h, "revenue", "ccagr", "revenue")[1], "+10.00% 4-yr CAGR (FY22–FY26)", "TCS CAGR uses its fixed FY2022-FY2026 window"); eq(row(h, "revenue", "ccagr", "revenue")[2], "+10.00% 3-yr CAGR (FY23–FY26)", "INFY CAGR uses FY2023-FY2026");
for (const k of ["eps_basic", "operating_cash_flow"]) { eq(row(h, k, "cchg", k), null, k + ": no change row"); eq(row(h, k, "ccagr", k), null, k + ": no CAGR row"); }
for (const k of ["revenue", "profit_before_tax", "profit_after_tax"]) { ok(row(h, k, "cchg", k) && row(h, k, "ccagr", k), k + ": change and CAGR rows exist"); }
re(h, /data-mark="official"/, "the official annual report marker is present"); re(h, /AR: the figure for that year was taken from the company's annual report/, "and explained");
no(h, /data-mark="differs"/, "no mismatch marker without a mismatched year"); no(h, /Change and CAGR are not calculated across FY2022 to FY2023 for ITC/, "no ITC note without ITC");
re(h, /EPS is shown as reported for each year, without adjustment for splits or bonus issues/, "the EPS no-adjustment note"); re(h, /not directly comparable across companies/, "and the EPS cross-company note");
no(h, /Total income/, "no bank wording for two non-banks"); no(h, /data-cnotice/, "no notices for a clean request"); re(h, /data-picker="1"/, "the picker is shown above the comparison"); ok(h.indexOf("data-picker") < h.indexOf("data-compare="), "picker comes first");
re(h, /<a href="#stock=TCS">TCS<\/a>/, "each column heading links to that stock's detail page"); re(h, /data-compare="TCS,INFY"/, "the comparison block names its stocks");

// order follows the request
h = C.build("#compare=INFY,TCS", D()); eq(heads(h, "revenue"), ["INFY", "TCS"], "column order is the requested order, not alphabetical or sorted by any value");
// ITC
h = C.build("#compare=ITC,TCS", D());
eq(row(h, "revenue", "fy", "2022"), ["FY2022", "50 AR", "100 AR"], "ITC FY2022 official figure");
eq(row(h, "revenue", "fy", "2024"), ["FY2024", "110 SD", "121"], "ITC mismatch years carry the SD marker"); re(h, /data-mark="differs"/, "SD present"); re(h, /SD: the source's own summary lines did not agree/, "SD explained");
re(h, /Change and CAGR are not calculated across FY2022 to FY2023 for ITC\./, "ITC boundary note"); eq(row(h, "revenue", "ccagr", "revenue").slice(1), ["+10.00% 3-yr CAGR (FY23–FY26)", "+10.00% 4-yr CAGR (FY22–FY26)"], "ITC uses FY2023-FY2026, TCS FY2022-FY2026");
// banks and non-banks
h = C.build("#compare=HDFCBANK,TCS,SBIN", D());
eq(tables(h), ["overview", "revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "operating_cash_flow"], "a mixed set has a separate Revenue table and Total income table");
eq(heads(h, "revenue"), ["TCS"], "Revenue lists only the non-bank"); eq(heads(h, "total_revenue"), ["HDFCBANK", "SBIN"], "Total income lists only the banks");
re(h, /<h4[^>]*data-ctitle="revenue">Revenue \(non-bank companies\)<\/h4>/, "the history table is titled Revenue (non-bank companies)"); re(h, /<h4[^>]*data-ctitle="total_revenue">Total income \(banks\)<\/h4>/, "and the bank table Total income (banks)");
re(text(h), /Banks report total income, which is not the same measure as revenue, so the two are shown in separate tables and are not compared/, "and explained");
eq(heads(h, "profit_after_tax"), ["HDFCBANK", "TCS", "SBIN"], "other tables keep the requested order");
const bankTbl = h.match(/<table data-ctable="total_revenue">.*?<\/table>/s)[0]; no(text(bankTbl), /Revenue/i, "the word Revenue never appears in the bank table");
h = C.build("#compare=HDFCBANK,SBIN", D()); eq(tables(h)[1], "total_revenue", "all banks: Total income table only"); no(tables(h).join(), /(^|,)revenue(,|$)/, "no Revenue table for banks"); no(text(h.slice(h.indexOf('data-compare='))), /Revenue/i, "the word Revenue is not used anywhere in an all-bank comparison");
re(h, /Bank operating cash flow includes deposit and lending movements and is not comparable with other companies/, "bank cash-flow note"); re(h, /<h4[^>]*>Total income<\/h4>/, "titled Total income");
h = C.build("#compare=TCS,INFY", D()); no(h, /Bank operating cash flow/, "no bank note for non-banks");
// gaps, negatives
h = C.build("#compare=GAPPY,NEGLAST", D());
eq(row(h, "revenue", "fy", "2023"), ["FY2023", "10", "-"], "GAPPY FY2023"); eq(row(h, "revenue", "fy", "2025"), ["FY2025", "-", "10"], "GAPPY has no FY2025: a dash, not a carried-forward value"); eq(row(h, "revenue", "fy", "2026"), ["FY2026", "14", "-4"], "negative shown as is");
eq(row(h, "revenue", "cchg", "revenue").slice(1), ["-", "-140.00% FY26 vs FY25"], "GAPPY: latest year (FY2026) has no consecutive previous year, so no change; NEGLAST computed by the shared rule"); eq(row(h, "revenue", "ccagr", "revenue").slice(1), ["-", "-"], "no CAGR without every year in the window, and none when a value is not positive");
// a stock with data but no history, and missing fundamentals values
h = C.build("#compare=GAPPY,TCS", D()); eq(row(h, "profit_before_tax", "fy", "2023"), ["FY2023", "-", "55"], "a year on file whose value is null shows a dash, not 0"); eq(row(h, "eps_basic", "fy", "2024"), ["FY2024", "-", "11.00"], "null EPS shows a dash");
h = C.build("#compare=NOHIST,TCS", D()); re(text(h), /No financial history is on file for NOHIST\./, "a stock with no history is named"); eq(heads(h, "revenue"), ["TCS"], "and left out of the history tables"); eq(row(h, "overview", "crow", "basis").slice(1), ["-", "consolidated"], "basis dash");
h = C.build("#compare=SBIN,TCS", D()); eq(row(h, "overview", "crow", "ev_ebitda").slice(1), ["-", "28.00".replace("28.00", "10.00")], "a null EV/EBITDA shows a dash");
h = C.build("#compare=TCS,ITC", D({ prices: null })); eq(row(h, "overview", "crow", "ltp").slice(1), ["-", "-"], "no price file: LTP dashes"); eq(row(h, "overview", "crow", "change").slice(1), ["-", "-"], "and change dashes"); no(h, /last daily close/, "and no price note");
// price rules, one case at a time
const px = (syms) => { const hh = C.build("#compare=" + syms.join(","), D()); return { ltp: row(hh, "overview", "crow", "ltp").slice(1), chg: row(hh, "overview", "crow", "change").slice(1), h: hh }; };
let q = px(["TCS", "ITC"]); eq(q.chg, ["+1.25%", "0.00%"], "a flat close is 0.00%, not a dash"); eq(q.ltp, ["3,442.50", "400.00"], "LTP of a flat stock");
q = px(["LT", "TCS"]); eq(q.ltp, ["-", "3,442.50"], "one candle only: LTP is a dash"); eq(q.chg, ["-", "+1.25%"], "and change is a dash");
q = px(["RELIANCE", "TCS"]); eq(q.ltp, ["-", "3,442.50"], "no candles: dashes"); eq(q.chg[0], "-", "no candles: no change");
q = px(["MARUTI", "TCS"]); eq(q.ltp, ["100.00", "3,442.50"], "a previous close of 0: LTP still shown"); eq(q.chg[0], "-", "but no change (division by zero)");
q = px(["HDFCBANK", "TCS"]); eq(q.ltp[0], "1,870.00", "junk candles (null close, bad date, missing date, null entry) are skipped: LTP is the last valid close"); eq(q.chg[0], "+10.00%", "and change is against the previous VALID candle");
q = px(["SBIN", "TCS"]); eq(q.ltp[0], "800.00", "candles out of file order are ordered by date: latest is 2026-10-03"); eq(q.chg[0], "+1.27%", "and the previous candle is 2026-10-02 (790)");
q = px(["NOPE", "TCS", "ITC"]); eq(q.ltp, ["3,442.50", "400.00"], "a symbol that is not in the data is dropped, the rest unaffected");
q = px(["TCS", "INFY"]); re(text(q.h), /as of 2026-10-02 \(not a live price\)/, "same last date: stated once");
q = px(["TCS", "SBIN"]); re(text(q.h), /last daily close in the historical data \(not a live price\)\. Last candle: TCS 2026-10-02, SBIN 2026-10-03\./, "different last dates: each stock's date is listed"); no(text(q.h), /as of 2026-10-0[23] \(not/, "and no single date is claimed");
q = px(["LT", "TCS"]); re(text(q.h), /as of 2026-10-02 \(not a live price\)/, "a stock with no valid price does not break the date note"); q = px(["LT", "RELIANCE"]); no(text(q.h), /last daily close/, "no priced stock: no price note");
for (const bad of [{}, { stocks: [] }, { stocks: { TCS: null } }, { stocks: { TCS: { candles: "x" } } }, { stocks: { TCS: { candles: [cd("2026-10-02", NaN), cd("2026-10-01", Infinity)] } } }, { stocks: { TCS: { candles: [cd("2026-10-02", "100"), cd("2026-10-01", 90)] } } }]) eq(row(C.build("#compare=TCS,ITC", D({ prices: bad })), "overview", "crow", "ltp").slice(1, 2), ["-"], "malformed price file " + JSON.stringify(bad).slice(0, 50) + ": dash, no error");
{ const rr = row(C.build("#compare=TCS,ITC", D({ prices: { stocks: { TCS: { candles: [cd("2026-10-01", -50), cd("2026-10-02", 100)] } } } })), "overview", "crow", "change"); eq(rr[1], "-", "a previous close of zero or less gives no change figure"); }
const dd0 = D(), snap0 = JSON.stringify(dd0.prices); C.build("#compare=SBIN,TCS", dd0); eq(JSON.stringify(dd0.prices), snap0, "reading prices never reorders or changes the candles in the data");
no(C.build("#compare=TCS,INFY", D()), /Price returns|1M|6M|1Y/, "no price returns");
h = C.build("#compare=TCS,ITC", D({ hist: null })); re(h, /data-cstate="history">Financial history unavailable\./, "history file unavailable: stated"); eq(tables(h), ["overview"], "overview still shown"); eq(row(h, "overview", "crow", "pe").slice(1).length, 2, "with the fundamentals");
h = C.build("#compare=TCS,ITC", D({ hist: { stocks: [] } })); re(h, /Financial history unavailable\./, "a malformed history file is unavailable, not an error");
h = C.build("#compare=TCS,ITC", { val: null, hist: null, prices: null }); re(h, /data-cstate="unavailable">Comparison data is unavailable right now\./, "no data at all: one clear message"); no(h, /data-picker/, "and no picker");
const savedF = window.SLFinHistory; delete window.SLFinHistory; h = C.build("#compare=TCS,ITC", D()); re(h, /Financial history unavailable\./, "without the history module the history part is unavailable, the page does not break"); window.SLFinHistory = savedF;

// the 2-5 rule in the view
const tblCount = (h) => (h.match(/data-ctable=/g) || []).length;
for (const hash of ["#compare=TCS", "#compare=", "#compare=TCS,TCS", "#compare=tcs,TCS,Tcs", "#compare=NOPE,TCS", "#compare=NOPE,ALSO"]) { h = C.build(hash, D()); eq(tblCount(h), 0, hash + ": fewer than two valid stocks shows no comparison"); re(h, /data-cnotice="count">Choose 2 to 5 stocks to compare\./, hash + ": says what to do"); re(h, /data-picker/, hash + ": shows the picker"); }
h = C.build("#compare=TCS", D()); re(h, /<input type="checkbox" data-sym="TCS" checked/, "the one valid stock stays ticked in the picker"); eq((h.match(/ checked/g) || []).length, 1, "and only that one");
re(C.build("#compare=NOPE,TCS", D()), /data-cnotice="unknown">Not in the current data: NOPE\./, "unknown symbol named");
re(C.build("#compare=<b>,TCS,ITC", D()), /data-cnotice="invalid">Not a valid stock symbol: &lt;B&gt;\./, "invalid symbol named, escaped"); eq(tblCount(C.build("#compare=<b>,TCS,ITC", D())) > 0, true, "and the two valid stocks are still compared");
for (const n of [2, 3, 4, 5]) { h = C.build("#compare=" + SYMS.slice(0, n).join(","), D()); eq(heads(h, "overview").length, n, n + " stocks: " + n + " columns"); no(h, /data-cnotice/, n + " stocks: no notice"); }
h = C.build("#compare=TCS,ITC,INFY,RELIANCE,LT,MARUTI,SBIN", D());
eq(heads(h, "overview"), ["TCS", "ITC", "INFY", "RELIANCE", "LT"], "seven requested: the first five are compared"); re(text(h), /Only 5 stocks can be compared\. The first 5 are shown; not included: MARUTI, SBIN\./, "with a clear notice naming the rest"); eq((h.match(/data-cnotice="extra"/g) || []).length, 1, "shown once");
h = C.build("#compare=TCS,ITC,INFY,RELIANCE,LT,MARUTI", D()); eq(heads(h, "overview").length, 5, "six requested: five shown"); re(h, /not included: MARUTI\./, "the sixth is named");
h = C.build("#compare=TCS,ITC,INFY,RELIANCE,LT", D()); eq((h.match(/<input type="checkbox"[^>]*disabled/g) || []).length, SYMS.length - 5, "with five selected every other checkbox is disabled"); eq((h.match(/<input type="checkbox"[^>]*checked[^>]*disabled/g) || []).length, 0, "and the ticked ones are not"); no(h, /data-apply="1"[^>]*disabled/, "Compare stays enabled at five"); re(h, /5 selected\. The maximum of 5 is selected\./, "status says so");
// the picker route (no comparison, no one-stock URL)
h = C.build("#compare-pick=TCS", D()); eq(tblCount(h), 0, "picker route: no comparison"); no(h, /data-cnotice="count"/, "and no error notice"); re(h, /data-sym="TCS" checked/, "the stock is preselected"); eq((h.match(/ checked/g) || []).length, 1, "only it"); re(h, /data-apply="1"[^>]*disabled/, "Compare is disabled with one stock"); re(h, /1 selected\. Choose at least 2\./, "status text");
h = C.build("#compare-pick", D()); eq((h.match(/ checked/g) || []).length, 0, "no preselection"); re(h, /0 selected\. Choose at least 2\./, "zero selected"); eq((h.match(/<input type="checkbox"/g) || []).length, SYMS.length, "every stock with data is listed"); no(h, /<input[^>]*disabled/, "none disabled");
h = C.build("#compare-pick=TCS,ITC", D()); no(h, /data-apply="1"[^>]*disabled/, "two preselected: Compare enabled"); eq(tblCount(h), 0, "but the picker route never shows a comparison by itself");
h = C.build("#compare-pick=TCS", D({ hist: null })); re(h, /data-sym="TCS" checked/, "the picker lists stocks from fundamentals when history is missing");
re(C.build("#compare-pick", D()), /Larsen &lt;b&gt;&amp;&lt;\/b&gt; Toubro/, "company names are escaped"); no(C.build("#compare-pick", D()), /<b>&<\/b>/, "never raw");
eq(C.build("#stock=TCS", D()).includes("data-picker"), false, "a non-compare hash renders nothing but the heading");
eq(C.knownOf(D()).TCS, 1, "known symbols come from fundamentals"); eq(C.knownOf({ val: null, hist: { stocks: { ZZZ: {} } } }), { ZZZ: 1 }, "or the history file"); eq(C.knownOf({ val: { stocks: [{ symbol: "<x>" }, null, { symbol: "OK" }] }, hist: null }), { OK: 1 }, "invalid symbols are ignored"); eq(C.knownOf(null), {}, "null data");

// neutral wording
const WORDS = /\b(best|worst|top|cheap|cheapest|expensive|undervalued|overvalued|outperform\w*|underperform\w*|buy|sell|hold|strong|weak|stronger|weaker|better|worse|winner|loser|leader|laggard|rank\w*|rated?|rating|score\w*|signals?|recommend\w*|target|should|advice|advise)\b/i;
for (const hash of ["#compare=TCS,INFY", "#compare=ITC,HDFCBANK,SBIN", "#compare=TCS,ITC,INFY,RELIANCE,LT,MARUTI,NOPE", "#compare=TCS"]) {
  const t = text(C.build(hash, D())).replace("Side-by-side data only. It is not a ranking, rating, recommendation or advice.", ""); no(t, WORDS, hash + ": no ranking / advice wording"); }
re(text(C.build("#compare=TCS,INFY", D())), /Side-by-side data only\. It is not a ranking, rating, recommendation or advice\./, "the neutral disclaimer is shown");
no(C.build("#compare=TCS,INFY", D()), /class="[^"]*\b(up|dn)\b/, "no good/bad colour classes on any figure");
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
ok(same(C.build("#compare=TCS,ITC", D()), C.build("#compare=TCS,ITC", D())), "building twice gives the same output");
ok(!same(C.build("#compare=TCS,ITC", D()), C.build("#compare=ITC,TCS", D())), "and the order of the request decides the order of the columns");
// no mutation of the data
const dd = D(), snap = JSON.stringify(dd); C.build("#compare=TCS,ITC,HDFCBANK", dd); eq(JSON.stringify(dd), snap, "building never changes the data it reads");

// ================= 5. in the page: routing, entry points, files read =================
function boot(hash, { files = null, delay = 0, withFH = true, presetFetch = null } = {}) {
  const fetched = [], win = {}, listeners = {}, classes = new Set(), headNodes = [], navKids = [];
  let detailHtml = "", detailKids = [];
  const parse = (htmlStr) => { const nodes = []; for (const m of htmlStr.matchAll(/<input type="checkbox" data-sym="([^"]+)"( checked)?( disabled)?/g)) nodes.push({ sym: m[1], checked: !!m[2], disabled: !!m[3], getAttribute(k) { return k === "data-sym" ? this.sym : null; } }); return nodes; };
  const mkEl = (tag) => { const e = { tag, id: "", attrs: {}, parentNode: null, _html: "", inputs: [], status: { textContent: "" }, applyBtn: { disabled: false }, textContent: "",
    set innerHTML(v) { (e.log = e.log || []).push(v); e._html = v; e.inputs = parse(v); e.status = { textContent: "" }; e.applyBtn = { disabled: /data-apply="1"[^>]*disabled/.test(v) }; }, get innerHTML() { return e._html; },
    setAttribute(k, v) { e.attrs[k] = v; }, getAttribute: (k) => e.attrs[k], addEventListener(t, f) { (e.l = e.l || {})[t] = f; },
    querySelectorAll: (s) => (s === "input[data-sym]" ? e.inputs : []), querySelector: (s) => (s === "[data-picker-status]" ? e.status : s === "[data-apply]" ? e.applyBtn : null),
    appendChild(c) { c.parentNode = e; if (tag === "nav") navKids.push(c); return c; }, insertBefore(c, ref) { c.parentNode = e; return c; } }; return e; };
  const parentW = { insertBefore: (c) => { c.parentNode = parentW; sectionKids.push(c); return c; } }, sectionKids = [];
  const detail = { id: "detail", parentNode: parentW, firstElementChild: null, firstChild: null, get innerHTML() { return detailHtml + detailKids.map((k) => "<" + k.tag + ' id="' + k.id + '">' + k.innerHTML + "</" + k.tag + ">").join(""); }, set innerHTML(v) { detailHtml = v; detailKids = []; observers.forEach((f) => queueMicrotask(f)); },
    insertBefore(c, ref) { c.parentNode = detail; detailKids.push(c); observers.forEach((f) => queueMicrotask(f)); return c; }, setAttribute() {}, addEventListener() {} };
  const observers = []; const nav = mkEl("nav"); nav.querySelector = (s) => (s === "#navCompare" ? navKids.find((k) => k.id === "navCompare") || null : null);
  global.MutationObserver = function (cb) { this.observe = (el) => { if (el === detail) observers.push(cb); }; };
  global.window = { location: { hash }, addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); }, scrollTo() {} };
  global.document = { body: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } }, head: { appendChild: (n) => headNodes.push(n) },
    getElementById: (i) => (i === "detail" ? detail : sectionKids.find((s) => s.id === i) || detailKids.find((k) => k.id === i) || null),
    querySelector: (s) => (s === 'nav[aria-label="Main"]' ? nav : null), createElement: (t) => mkEl(t) };
  const f = files || { "fundamentals.json": FUND(), "financial_history.json": HDOC(), "historical.json": PRICES() };
  global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); const x = f[u.split("/").pop()]; if (x === "reject") throw new Error("net"); return { ok: x !== undefined && x !== null, json: async () => JSON.parse(JSON.stringify(x)) }; };
  if (withFH) (0, eval)(fhCode);
  (0, eval)(cmpCode);
  const fire = () => (listeners.hashchange || []).forEach((fn) => fn());
  return { fetched, classes, headNodes, navKids, detail, sectionKids, nav, fire,
    section: () => sectionKids.find((s) => s.id === "compareView"), html: () => (sectionKids.find((s) => s.id === "compareView") || {}).innerHTML || "",
    go: async (h2, ms = 40 + delay * 3) => { window.location.hash = h2; fire(); await sleep(ms); },
    setDetail: async (v) => { detail.innerHTML = v; await sleep(10); }, detailText: () => detail.innerHTML, win: () => window };
}
const DETAILHTML = (s) => '<p><button id="detailBack">Back</button></p><h2>Stock Detail: ' + s + '</h2><div id="detailChart"></div>';
(async () => {
  let t = await boot("", {}); await sleep(30);
  eq(t.navKids.length, 1, "the header gets one link"); eq(t.navKids[0].attrs.href, "#compare-pick", "that opens the picker, not a comparison"); eq(t.navKids[0].textContent, "Compare Stocks", "labelled Compare Stocks");
  eq(t.navKids[0].id, "navCompare", "with an id so it is added once"); ok(!t.classes.has("compare-mode"), "no compare mode on the dashboard"); eq(t.fetched.length, 0, "and no request is made until the compare view is opened");
  eq(t.headNodes.length, 0, "no style is injected until the compare view opens"); ok(!t.section(), "and no section either");
  t = await boot("#compare=TCS,INFY"); await sleep(60);
  ok(t.classes.has("compare-mode"), "compare mode on for a compare hash"); re(t.html(), /data-ctable="overview"/, "the comparison renders into its own section"); eq(t.section().parentNode !== null, true, "placed in the page before #detail");
  eq(t.headNodes.length, 1, "one style element"); re(t.headNodes[0].textContent, /\.compare-mode \.w>\*:not\(#compareView\)\{display:none!important\}/, "it hides the rest of the page only in compare mode"); re(t.headNodes[0].textContent, /#compareView\{display:none\}/, "and keeps the section hidden otherwise");
  eq([...t.fetched].sort(), ["out/financial_history.json", "out/fundamentals.json", "out/historical.json"], "exactly three requests, one per file");
  ok(t.fetched.every((u) => /^out\/(fundamentals|financial_history|historical)\.json$/.test(u)), "static files under out/ only"); ok(!t.fetched.some((u) => /scans|financials\.json|http|upstox/i.test(u)), "no scans file, no statements file, no outside address");
  await t.go("#compare=ITC,TCS,LT"); eq(t.fetched.length, 3, "changing the selection makes no new request"); re(t.html(), /data-compare="ITC,TCS,LT"/, "but re-renders the new selection");
  await t.go("#compare-pick=TCS"); re(t.html(), /data-sym="TCS" checked/, "picker route renders the picker"); no(t.html(), /data-ctable/, "with no comparison"); ok(t.classes.has("compare-mode"), "still in compare mode");
  await t.go("#stock=TCS"); ok(!t.classes.has("compare-mode"), "a stock hash leaves compare mode"); await t.go(""); ok(!t.classes.has("compare-mode"), "so does an empty hash"); await t.go("#demo"); ok(!t.classes.has("compare-mode"), "and an anchor");
  await t.go("#compare=TCS"); re(t.html(), /data-cnotice="count"/, "one stock typed in the URL: told to choose 2 to 5"); no(t.html(), /data-ctable/, "no comparison"); eq(t.fetched.length, 3, "still three requests in total");
  // the picker's behaviour, through the page's own listeners
  await t.go("#compare-pick=TCS");
  const sec = t.section(), fire = (type, ev) => sec.l[type](ev), inputs = () => sec.inputs;
  const tick = (s, on) => { const i = inputs().find((x) => x.sym === s); i.checked = on; fire("change", { target: i }); };
  const applyEv = () => ({ target: { closest: (q) => (q === "[data-apply]" ? {} : null) } });
  eq(typeof sec.l.change, "function", "the section listens for changes"); eq(typeof sec.l.click, "function", "and clicks");
  fire("click", applyEv()); eq(window.location.hash, "#compare-pick=TCS", "Compare with one stock does nothing: no one-stock comparison URL is made");
  tick("ITC", true); eq(sec.applyBtn.disabled, false, "two ticked: Compare enabled"); re(sec.status.textContent, /^2 selected\./, "status updates");
  tick("INFY", true); tick("LT", true); tick("SBIN", true); eq(sec.status.textContent, "5 selected. The maximum of 5 is selected.", "five ticked"); eq(inputs().filter((i) => i.disabled).length, SYMS.length - 5, "all others disabled at five"); eq(inputs().filter((i) => i.disabled && i.checked).length, 0, "ticked ones stay enabled");
  const sixth = inputs().find((i) => i.sym === "MARUTI"); sixth.checked = true; fire("change", { target: sixth }); eq(inputs().filter((i) => i.checked).length, 5, "a sixth tick (forced) is refused and reverted"); eq(sixth.checked, false, "the sixth box is unticked again");
  tick("TCS", false); eq(inputs().filter((i) => i.disabled).length, 0, "unticking re-enables the rest"); eq(sec.status.textContent, "4 selected. You can add up to 1 more.", "status text");
  tick("TCS", true); fire("click", applyEv()); eq("#" + window.location.hash.replace(/^#/, ""), "#compare=ITC,INFY,LT,SBIN,TCS", "Compare builds the URL in the order the stocks were ticked");
  tick("ITC", false); tick("INFY", false); tick("LT", false); tick("SBIN", false); eq(sec.applyBtn.disabled, true, "down to one: Compare disabled"); const before = window.location.hash; fire("click", applyEv()); eq(window.location.hash, before, "and pressing it changes nothing");
  fire("click", { target: { closest: (q) => (q === "[data-back]" ? {} : null) } }); eq(window.location.hash, "", "Back clears the hash");
  fire("change", { target: { getAttribute: () => null } }); fire("change", { target: null }); fire("click", { target: null }); fire("click", { target: { closest: () => null } }); ok(true, "stray events are ignored");
  // detail entry point
  t = await boot("#stock=TCS"); await t.setDetail(DETAILHTML("TCS"));
  eq(t.detail.innerHTML.match(/id="detailCompare"/g).length, 1, "Stock Detail gets exactly one Compare with… link"); re(t.detail.innerHTML, /<a href="#compare-pick=TCS" data-compare-with="TCS">Compare with…<\/a>/, "pointing at the picker with this stock preselected");
  no(t.detail.innerHTML, /#compare=/, "never at a one-stock comparison URL"); await sleep(30); eq(t.detail.innerHTML.match(/id="detailCompare"/g).length, 1, "still one after settling");
  await t.setDetail(DETAILHTML("ITC")); await t.go("#stock=ITC", 5); await t.setDetail(DETAILHTML("ITC")); re(t.detail.innerHTML, /#compare-pick=ITC/, "re-added after the detail view re-renders, for the new stock"); eq(t.detail.innerHTML.match(/id="detailCompare"/g).length, 1, "once");
  await t.setDetail('<p class="note">Loading TCS…</p>'); no(t.detail.innerHTML, /detailCompare/, "not added while the detail view is still loading");
  t = await boot("#compare=TCS,ITC"); await t.setDetail(DETAILHTML("TCS")); no(t.detail.innerHTML, /detailCompare/, "not added when the route is not a stock page");
  t = await boot("#stock=%3Cb%3E"); await t.setDetail(DETAILHTML("X")); no(t.detail.innerHTML, /detailCompare/, "not added for an invalid symbol");
  // failure modes
  t = await boot("#compare=TCS,ITC", { files: { "fundamentals.json": "reject", "financial_history.json": "reject", "historical.json": "reject" } }); await sleep(50); re(t.html(), /Comparison data is unavailable right now\./, "every file failing: a clear message, no exception");
  t = await boot("#compare=TCS,ITC", { files: { "fundamentals.json": FUND(), "financial_history.json": null, "historical.json": null } }); await sleep(50); re(t.html(), /data-ctable="overview"/, "history and prices missing: the overview still renders"); re(t.html(), /Financial history unavailable\./, "and says so");
  t = await boot("#compare=TCS,ITC", { withFH: false }); await sleep(50); re(t.html(), /Financial history unavailable\./, "without the history module the page still renders");
  t = await boot("#compare=TCS,ITC", { delay: 30 }); await t.go("#compare=INFY,LT", 200); re(t.html(), /data-compare="INFY,LT"/, "a slow earlier load never overwrites a newer selection"); ok(!t.section().log.slice(1).some((x) => /data-compare="TCS,ITC"/.test(x)), "and the stale selection is never drawn at all"); no(t.html(), /data-compare="TCS,ITC"/, "the stale one is discarded");
  t = await boot(""); await sleep(20); await t.go("#compare=TCS,ITC", 5); await t.go("#stock=TCS", 150); ok(!t.classes.has("compare-mode"), "leaving while a load is pending leaves compare mode off");

  // ================= 6. nothing else moved =================
  eq(blocks.length, 7, "the page still has exactly the seven classic script blocks"); eq(html.split(FHTAG).length, 2, "one Financial History module (exact tag)"); eq(html.split(CMPTAG).length, 2, "one compare module"); eq((html.match(/<script/g) || []).length, 9, "nine script elements in all");
  const BASE = "7748874"; let old = ""; try { old = cp.execSync("git show " + BASE + ":index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { old = ""; }
  if (old) {
    const ob = old.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
    eq(blocks.filter((b) => !b.includes("Stock Detail view")).map(sha), ob.filter((b) => !b.includes("Stock Detail view")).map(sha), "every classic script block except the Stock Detail block (Phase 5D.1) is byte-for-byte identical to Phase 5A (" + BASE + ")");
    eq(sha(fhCode), sha((old.split(FHTAG)[1] || "").split("</script>")[0]), "the Financial History module is byte-for-byte identical to Phase 5A");
    const strip = (x) => x.replace(/<script type="module" id="stocklens-compare">[\s\S]*?<\/script>\n/, "");
    const ND = ((x) => x.split("<script>").map((q, i) => (i && q.split("</script>")[0].includes("Stock Detail view") ? "</script>" + q.split("</script>").slice(1).join("</script>") : q)).join("<script>")); eq(ND(strip(html)), ND(old), "removing the compare module gives back the Phase 5A page exactly, apart from the Stock Detail block (Phase 5D.1): nothing else in index.html changed");
  }
  eq((cmpCode.match(/fetch\(/g) || []).length, 1, "the compare module makes one fetch call (in one helper)"); eq([...new Set(cmpCode.match(/out\/[a-z_]+\.json/g))].sort(), ["out/financial_history.json", "out/fundamentals.json", "out/historical.json"], "reading exactly three files");
  no(cmpCode, /localStorage|sessionStorage|indexedDB|XMLHttpRequest|eval\(|document\.write|<script|https?:|scans\.json|financials\.json|innerHTML\s*\+=|Function\(/, "no storage, no outside address, no scans file, no statements file, no eval");
  no(cmpCode, /createChart|LightweightCharts|<canvas/i, "no chart library and no canvas (Phase 5C charts are inline SVG)");
  no(cmpCode, /Phase 4 Step 4E/, "the module does not copy the history module"); ok(!/getElementById\("detail(Research|Checklist|Tech|Chart)"\)/.test(cmpCode), "and does not reach into the other detail sections");
  re(html, /<style>[\s\S]*?<\/style><\/head>/, "the page's own stylesheet is still where it was"); no(html.split(CMPTAG)[0].split("<style>")[1].split("</style>")[0], /compare/i, "and has no compare rules: they are injected by the module");
  console.log("Financial history compare tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
