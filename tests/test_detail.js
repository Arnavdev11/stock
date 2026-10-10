// Run: node tests/test_detail.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Stock Detail view (#stock=SYMBOL) added in Phase 3 Step 1. Read-only.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const PINHTML5J = require("./legacy_5i.js").legacy(html); // pre-5J homepage, for assertions about the removed old dashboard script
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const code = blocks.find((b) => b.includes("Stock Detail view"));
assert.ok(code, "detail script block exists");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };

const FIN_STOCK = (sym, o = {}) => Object.assign({ symbol: sym, company_name: sym + " LIMITED", basis: "consolidated",
  income: { period: "Mar 2025", basis: "consolidated", revenue: 1000000, total_revenue: 1010000, profit_before_tax: 200000, profit_after_tax: 150000, eps_basic: 41.5, eps_diluted: 41.4 },
  balance_sheet: { period: "Mar 2025", basis: "consolidated", total_assets: 5000, total_liabilities: 3000, total_equity: 2000, liabilities_to_equity: 1.5, total_debt: null, debt_to_equity: null },
  cash_flow: { period: "Mar 2025", basis: "consolidated", operating: 400, investing: -300, financing: -50, free_cash_flow: null } }, o);
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: [FIN_STOCK("TCS"),
  FIN_STOCK("INFY", { income: { period: "Mar 2025", basis: "standalone", revenue: null, total_revenue: 5, profit_before_tax: null, profit_after_tax: null, eps_basic: null, eps_diluted: null },
                      cash_flow: { period: "Mar 2025", basis: "consolidated", operating: 400, investing: -300, financing: null, free_cash_flow: null } })] };
const VAL = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TCS LIMITED", sector: "IT Services", pe: 25.5, pb: 12, roe: 45, roce: 55, roa: 20, ev_ebitda: 18 },
  { symbol: "INFY", company_name: "INFY LIMITED", sector: null, pe: null, pb: 7, roe: 30, roce: null, roa: 15, ev_ebitda: null }] };
// out/historical.json: the only source of the price tiles. Candles are deliberately unsorted and partly invalid.
const C = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const HIST = { updated: "2026-10-01", source: "Upstox", stocks: {
  TCS: { symbol: "TCS", candles: [C("2026-10-01", 4000.5), C("2026-09-29", 3900), C("2026-09-30", 3950), C("bad-date", 9999), C("2026-10-02", "4100"), C("2026-10-03", null), C("20261004", 1), C("2026-13-45x", 1), { date: 20261005, close: 1 }, { date: "2026-10-04" }, null, 7] },
  INFY: { symbol: "INFY", candles: [C("2026-10-01", 1500), C("2026-09-30", "x"), C("2026-09-29", null)] } } };
// a scans.json whose numbers must never reach the page any more
const SCANS = { as_of: "2026-10-01", gainers: [{ symbol: "TCS", price: 1, change_pct: 99 }], losers: [], volume_gainers: [], high_52w: [], delivery: [], dma: [{ symbol: "TCS", price: 1 }, { symbol: "INFY", price: 2 }, { symbol: "XYZ", price: 3 }] };

async function boot(hash, files = { "fundamentals.json": VAL, "financials.json": FIN, "historical.json": HIST, "scans.json": SCANS }) {
  const els = {}, listeners = {}, win = {}, classes = new Set(), fetched = [];
  const mk = (id) => ({ id, innerHTML: "", textContent: "" });
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } },
    getElementById: (i) => els[i] || (els[i] = mk(i)), addEventListener: (t, f) => { listeners[t] = f; } };
  global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(code);
  await new Promise((r) => setTimeout(r, 30));
  const nav = async (h) => { window.location.hash = h; win.hashchange(); await new Promise((r) => setTimeout(r, 30)); };
  return { els, listeners, classes, fetched, nav, detail: () => els.detail.innerHTML };
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tile = (h, label) => text((h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + label.replace(/[()/]/g, "\\$&") + "</span>")) || [])[1] || "NOT FOUND");
const cell = (sym, first = true) => { const td = { textContent: sym };
  td.parentNode = { firstElementChild: first ? td : {} }; td.closest = (s) => (s === "td" ? td : (s.includes("#t2a") ? {} : null)); return td; };   // like a real <td>: inside a stock table, not inside #detailBack

(async () => {
  // --- the dashboard is untouched when there is no #stock=SYMBOL hash
  let t = await boot("");
  ok(!t.classes.has("detail-mode"), "no detail view on the dashboard"); eq(t.fetched, [], "no data fetched until a stock is opened");
  for (const h of ["#stock", "#stock=", "#stock=<script>", "#stock=TCS&x=1", "#stock=12.5", "#other"]) { t = await boot(h); ok(!t.classes.has("detail-mode"), "ignored hash " + h); }

  // --- #stock=TCS opens the TCS detail view
  t = await boot("#stock=TCS");
  ok(t.classes.has("detail-mode")); let d = t.detail();
  re(d, /Stock Detail: TCS/); re(d, /← Back to StockLens/);
  eq(["out/fundamentals.json", "out/financials.json", "out/historical.json"].every((u) => t.fetched.includes(u)), true, "reads fundamentals, financials and historical");
  eq(t.fetched.length, 3, "still three requests"); ok(!t.fetched.some((u) => u.includes("scans")), "scans.json is not requested");
  // header
  eq(tile(d, "Symbol"), "TCS"); eq(tile(d, "Company"), "TCS LIMITED"); eq(tile(d, "Sector"), "IT Services");
  eq(tile(d, "Latest Price (last close, ₹)"), "4,000.50", "latest valid close, from historical.json (not the 1 in scans.json)"); eq(tile(d, "Day change vs previous close"), "+1.28%", "(4000.5 / 3950 - 1) * 100");
  eq(tile(d, "LTP (last close, ₹)"), "NOT FOUND", "old label gone"); eq(tile(d, "Today's change"), "NOT FOUND", "old label gone");
  re(d, /last daily close in the historical data, as of 2026-10-01 \(not a live price\)/, "the candle date is shown and the note says it is not live");
  no(d, /live LTP|latest end-of-day scan/i, "no live-price wording, no scan wording");
  // overview
  eq([tile(d, "P/E"), tile(d, "P/B"), tile(d, "ROE %"), tile(d, "ROCE %"), tile(d, "ROA %"), tile(d, "EV/EBITDA")], ["25.50", "12.00", "45.00", "55.00", "20.00", "18.00"]);
  // financial health (Indian grouping)
  eq([tile(d, "Revenue"), tile(d, "Total revenue"), tile(d, "Profit before tax"), tile(d, "Profit after tax"), tile(d, "EPS basic (₹)"), tile(d, "EPS diluted (₹)")],
     ["10,00,000", "10,10,000", "2,00,000", "1,50,000", "41.50", "41.40"]);
  eq([tile(d, "Total assets"), tile(d, "Total liabilities"), tile(d, "Total equity"), tile(d, "Liabilities / Equity"), tile(d, "Debt / Equity")], ["5,000", "3,000", "2,000", "1.50", "N/A"]);
  // cash flow: FCF is N/A and never operating + investing (400 - 300 = 100)
  eq([tile(d, "Operating cash flow"), tile(d, "Investing cash flow"), tile(d, "Financing cash flow"), tile(d, "Free cash flow")], ["400", "-300", "-50", "N/A"]);
  re(d, /Free Cash Flow is unavailable because Upstox did not provide an unambiguous capital-expenditure line for these stocks\./);
  re(d, /Liabilities \/ Equity is not Debt \/ Equity/);
  re(d, /Historical Price Chart — Coming in Phase 3 Step 2/);
  no(d, /operating profit/i); for (const bad of ["NaN", "undefined", "null", "Infinity"]) no(d, new RegExp(bad));

  // --- missing values show "-": INFY has no sector, revenue, PBT/PAT, EPS, P/E, ROCE, EV/EBITDA, financing; only one valid candle
  await t.nav("#stock=INFY"); d = t.detail(); re(d, /Stock Detail: INFY/);
  eq([tile(d, "Sector"), tile(d, "Revenue"), tile(d, "Profit before tax"), tile(d, "EPS basic (₹)"), tile(d, "P/E"), tile(d, "ROCE %"), tile(d, "EV/EBITDA"), tile(d, "Financing cash flow")],
     ["-", "-", "-", "-", "-", "-", "-", "-"]);
  eq([tile(d, "Latest Price (last close, ₹)"), tile(d, "Day change vs previous close"), tile(d, "Free cash flow"), tile(d, "Debt / Equity")], ["-", "-", "N/A", "N/A"], "one valid candle: no price, no change (scans.json price 2 is ignored)");
  no(d, /as of 20/, "and no date is claimed");
  re(d, /Mar 2025 · standalone/, "standalone is labelled");

  // --- a symbol with no data at all
  await t.nav("#stock=XYZ"); d = t.detail(); re(d, /Stock Detail: XYZ/); re(d, /No fundamentals or financial statements were found for XYZ/);
  eq([tile(d, "Company"), tile(d, "Latest Price (last close, ₹)"), tile(d, "P/E"), tile(d, "Total assets")], ["-", "-", "-", "-"], "a stock missing from historical.json: dashes, even though scans.json has a price for it");

  // --- navigation: clicking a symbol in a table sets the hash; Enter works; other cells and tables are ignored
  await t.nav("");
  ok(!t.classes.has("detail-mode"), "back to the dashboard");
  t.listeners.click({ target: cell("TCS") }); eq(window.location.hash, "stock=TCS", "clicking TCS sets #stock=TCS");
  window.location.hash = ""; t.listeners.click({ target: cell("12.5", true) }); eq(window.location.hash, "", "numbers are not symbols");
  t.listeners.click({ target: cell("TCS", false) }); eq(window.location.hash, "", "only the first cell opens a stock");
  t.listeners.click({ target: Object.assign(cell("TCS"), { closest: (s) => (s === "td" ? { ...cell("TCS"), closest: () => null } : null) }) }); eq(window.location.hash, "", "tables outside the stock lists are ignored");
  t.listeners.keydown({ key: "Enter", target: cell("INFY") }); eq(window.location.hash, "stock=INFY", "Enter on a symbol opens it");

  // --- NSE scan tables: scan tabs (#rows button) and tools tabs (#tbody, only when the first column is "Symbol")
  const scanBtn = (sym) => { const b = { textContent: sym }; return { closest: (s) => (s === "#rows button" ? b : null) }; };
  const toolTd = (text, header, tagged = false) => { const tbl = { querySelector: () => ({ textContent: header }) };
    const td = { textContent: tagged ? text + "SAMPLE" : text, firstChild: { nodeType: 3, nodeValue: text } };
    td.parentNode = { firstElementChild: td }; td.closest = (s) => (s === "td" ? td : s === "#tbody" ? {} : s === "table" ? tbl : null); return td; };
  window.location.hash = ""; t.listeners.click({ target: scanBtn("TCS") }); eq(window.location.hash, "stock=TCS", "scan table: clicking TCS opens #stock=TCS");
  window.location.hash = ""; t.listeners.click({ target: scanBtn("BAJAJ-AUTO") }); eq(window.location.hash, "stock=BAJAJ-AUTO", "scan table: any valid symbol");
  window.location.hash = ""; t.listeners.click({ target: scanBtn("M&M") }); eq(window.location.hash, "stock=M%26M", "scan table: symbols with & are encoded");
  window.location.hash = ""; t.listeners.click({ target: scanBtn("12.5") }); eq(window.location.hash, "", "scan table: non-symbols are ignored");
  window.location.hash = ""; t.listeners.click({ target: toolTd("TCS", "Symbol") }); eq(window.location.hash, "stock=TCS", "tools table: Symbol column opens the stock");
  window.location.hash = ""; t.listeners.click({ target: toolTd("GLENMARK", "Symbol", true) }); eq(window.location.hash, "stock=GLENMARK", "tools table: the SAMPLE tag is not part of the symbol");
  window.location.hash = ""; t.listeners.click({ target: toolTd("28-Sep-2025", "Date") }); eq(window.location.hash, "", "tools table: Date column (big deals, FII/DII) is not a stock link");
  window.location.hash = ""; t.listeners.click({ target: toolTd("TCS", "Investor") }); eq(window.location.hash, "", "tools table: other first columns are ignored");
  // the old inline handler that scrolled to the sample filings panel is gone from the scan rows; the filings section itself is still there
  no(html, /onclick="pick\(/, "no inline pick() call left in the scan table");
  re(PINHTML5J, /<button class="lk" type="button">\$\{r\[0\]\}<\/button>/, "scan rows still render the symbol as a button");
  ok(PINHTML5J.includes("function pick(k)") && PINHTML5J.includes('id="sp"'), "sample filings panel and its dropdown are unchanged");

  // --- Back control returns to the dashboard
  await t.nav("#stock=TCS"); ok(t.classes.has("detail-mode"));
  t.listeners.click({ target: { closest: (s) => (s === "#detailBack" ? {} : null) } }); eq(window.location.hash, "", "Back clears the hash");
  await t.nav(""); ok(!t.classes.has("detail-mode"), "dashboard visible again");

  // --- a failed data file does not break the page
  t = await boot("#stock=TCS", { "fundamentals.json": VAL }); d = t.detail();
  eq([tile(d, "P/E"), tile(d, "Total assets"), tile(d, "Latest Price (last close, ₹)"), tile(d, "Debt / Equity")], ["25.50", "-", "-", "N/A"]);
  re(d, /Financial statements not loaded yet/);

  // --- Phase 5D.1: the price tiles in detail
  const price = async (stock, hist, extra = {}) => { const tt = await boot("#stock=" + stock, Object.assign({ "fundamentals.json": VAL, "financials.json": FIN, "historical.json": hist }, extra)), dd = tt.detail();
    return { dd, p: tile(dd, "Latest Price (last close, ₹)"), c: tile(dd, "Day change vs previous close"), f: tt.fetched }; };
  const H = (stocks) => ({ stocks }); const cls = (h) => (h.match(/<b style="font-size:1.1rem" class="([^"]*)">[^<]*<\/b><span>Day change vs previous close/) || [])[1];
  let r = await price("TCS", H({ TCS: { candles: [C("2026-10-01", 110), C("2026-09-30", 100)] } })); eq([r.p, r.c], ["110.00", "+10.00%"], "latest and previous close"); eq(cls(r.dd), "up", "a rise is styled as a rise"); re(r.dd, /as of 2026-10-01/, "date shown");
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 90)] } })); eq([r.p, r.c], ["90.00", "-10.00%"], "a fall is signed -"); eq(cls(r.dd), " dn", "a fall is styled as a fall");
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 100)] } })); eq([r.p, r.c], ["100.00", "0.00%"], "no change is 0.00%"); eq(cls(r.dd), "", "and is not styled as a rise or a fall");
  r = await price("TCS", H({ TCS: { candles: [C("2026-10-01", 103), C("2026-09-30", 100), C("2026-09-29", 1), C("2026-10-02", 107), C("2026-10-03", 120)].reverse() } })); eq([r.p, r.c], ["120.00", "+12.15%"], "unsorted: ordered by date, not by position (107 -> 120)"); re(r.dd, /as of 2026-10-03/, "the date is the latest candle's");
  r = await price("TCS", H({ TCS: { candles: [C("2026-10-01", 110)] } })); eq([r.p, r.c], ["-", "-"], "one valid candle: both dashes"); no(r.dd, /as of 2026/, "no date");
  r = await price("TCS", H({ TCS: { candles: [] } })); eq([r.p, r.c], ["-", "-"], "no candles");
  r = await price("TCS", H({ TCS: {} })); eq([r.p, r.c], ["-", "-"], "no candles array");
  r = await price("TCS", H({})); eq([r.p, r.c], ["-", "-"], "stock missing from historical.json");
  r = await price("TCS", H({ INFY: { candles: [C("2026-10-01", 5), C("2026-09-30", 4)] } })); eq([r.p, r.c], ["-", "-"], "another stock's candles are not used");
  for (const bad of [null, [], "x", 5]) { r = await price("TCS", { stocks: bad }); eq([r.p, r.c], ["-", "-"], "stocks container " + JSON.stringify(bad)); }
  r = await price("TCS", null); eq([r.p, r.c], ["-", "-"], "historical.json not loaded");
  r = await price("TCS", H({ TCS: { candles: "nope" } })); eq([r.p, r.c], ["-", "-"], "candles not an array");
  // malformed dates are ignored before ordering: they must not become 'latest'
  for (const bad of ["bad-date", "2026-1-5", "20261005", "2026-10-05T00:00:00Z", "", " 2026-10-05", null, 20261005, {}, []]) { r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 110), C(bad, 5000)] } })); eq([r.p, r.c], ["110.00", "+10.00%"], "malformed date " + JSON.stringify(bad) + " ignored"); re(r.dd, /as of 2026-10-01/); }
  // non-numeric closes are ignored too
  for (const bad of ["110", "Infinity", "", null, undefined, true, {}, [], NaN]) { r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 110), { date: "2026-10-05", close: bad }, { date: "2026-10-04", close: bad }] } })); eq([r.p, r.c], ["110.00", "+10.00%"], "close " + String(bad) + " ignored: the previous valid day stays the latest"); }
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-29", 100), { date: "2026-09-30", close: "bad" }, C("2026-10-01", 110)] } })); eq([r.p, r.c], ["110.00", "+10.00%"], "an invalid close in the middle is skipped: the previous VALID close is used");
  // zero / negative previous close: the price stays, the change is a dash (never Infinity or NaN)
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 0), C("2026-10-01", 110)] } })); eq([r.p, r.c], ["110.00", "-"], "previous close 0: price shown, change -"); no(r.dd, /Infinity|NaN/);
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", -5), C("2026-10-01", 110)] } })); eq([r.p, r.c], ["110.00", "-"], "previous close negative: price shown, change -");
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 0)] } })); eq(r.p, "0.00", "the latest close is shown as it is on file"); eq(r.c, "-100.00%", "and the change follows from it");
  // duplicates: same rule as the comparison page (stable order of equal dates)
  // no dependency on scans.json at all
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 110)] } }), { "scans.json": null }); eq([r.p, r.c], ["110.00", "+10.00%"], "works with no scans.json at all");
  r = await price("XYZ", H({ XYZ: { candles: [C("2026-09-30", 200), C("2026-10-01", 210)] } })); eq([r.p, r.c], ["210.00", "+5.00%"], "a stock in neither fundamentals nor financials still shows its price"); re(r.dd, /No fundamentals or financial statements were found for XYZ/);
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 110)] } }), { "scans.json": { as_of: "x", gainers: [{ symbol: "TCS", price: 7, change_pct: 7 }] } }); eq([r.p, r.c], ["110.00", "+10.00%"], "scans.json values never win");
  ok(!/scans/.test(code.replace(/\/\*[\s\S]*?\*\//g, "").slice(code.indexOf("function loadData"), code.indexOf("function view"))), "the detail data loader names no scans file");
  for (const k of ["gainers", "losers", "volume_gainers", "high_52w", "delivery", "dma", "change_pct"]) no(code.slice(code.indexOf("function loadData"), code.indexOf("function view")), new RegExp(k), "detail price code does not read scan list " + k);
  // wording
  r = await price("TCS", H({ TCS: { candles: [C("2026-09-30", 100), C("2026-10-01", 110)] } }));
  no(text(r.dd), /\b(buy|sell|strong|bullish|bearish|score|rating|winner|target|signal|recommend|outperform|underperform)\b/i, "no advisory wording");
  re(r.dd, /\(not a live price\)/, "says it is not live"); no(r.dd, /live LTP/i, "never called live LTP");

  console.log("Detail view tests passed (" + checks + " checks)");
})().catch((x) => { console.error("DETAIL TEST FAILED:", x.message); process.exit(1); });
