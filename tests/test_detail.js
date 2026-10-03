// Run: node tests/test_detail.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Stock Detail view (#stock=SYMBOL) added in Phase 3 Step 1. Read-only.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
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
const SCANS = { as_of: "2026-10-01", gainers: [{ symbol: "TCS", price: 4000.5, change_pct: 1.25 }], losers: [], volume_gainers: [], high_52w: [], delivery: [],
  dma: [{ symbol: "TCS", price: 4000.5 }, { symbol: "INFY", price: 1500 }] };

async function boot(hash, files = { "fundamentals.json": VAL, "financials.json": FIN, "scans.json": SCANS }) {
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
  eq(["out/fundamentals.json", "out/financials.json", "out/scans.json"].every((u) => t.fetched.includes(u)), true, "reads the three existing JSON files");
  // header
  eq(tile(d, "Symbol"), "TCS"); eq(tile(d, "Company"), "TCS LIMITED"); eq(tile(d, "Sector"), "IT Services");
  eq(tile(d, "LTP (last close, ₹)"), "4,000.50"); eq(tile(d, "Today's change"), "+1.25%");
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

  // --- missing values show "-": INFY has no sector, revenue, PBT/PAT, EPS, P/E, ROCE, EV/EBITDA, financing; price only from the dma list
  await t.nav("#stock=INFY"); d = t.detail(); re(d, /Stock Detail: INFY/);
  eq([tile(d, "Sector"), tile(d, "Revenue"), tile(d, "Profit before tax"), tile(d, "EPS basic (₹)"), tile(d, "P/E"), tile(d, "ROCE %"), tile(d, "EV/EBITDA"), tile(d, "Financing cash flow")],
     ["-", "-", "-", "-", "-", "-", "-", "-"]);
  eq([tile(d, "LTP (last close, ₹)"), tile(d, "Today's change"), tile(d, "Free cash flow"), tile(d, "Debt / Equity")], ["1,500.00", "-", "N/A", "N/A"]);
  re(d, /Mar 2025 · standalone/, "standalone is labelled");

  // --- a symbol with no data at all
  await t.nav("#stock=XYZ"); d = t.detail(); re(d, /Stock Detail: XYZ/); re(d, /No fundamentals or financial statements were found for XYZ/);
  eq([tile(d, "Company"), tile(d, "LTP (last close, ₹)"), tile(d, "P/E"), tile(d, "Total assets")], ["-", "-", "-", "-"]);

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
  re(html, /<button class="lk" type="button">\$\{r\[0\]\}<\/button>/, "scan rows still render the symbol as a button");
  ok(html.includes("function pick(k)") && html.includes('id="sp"'), "sample filings panel and its dropdown are unchanged");

  // --- Back control returns to the dashboard
  await t.nav("#stock=TCS"); ok(t.classes.has("detail-mode"));
  t.listeners.click({ target: { closest: (s) => (s === "#detailBack" ? {} : null) } }); eq(window.location.hash, "", "Back clears the hash");
  await t.nav(""); ok(!t.classes.has("detail-mode"), "dashboard visible again");

  // --- a failed data file does not break the page
  t = await boot("#stock=TCS", { "fundamentals.json": VAL }); d = t.detail();
  eq([tile(d, "P/E"), tile(d, "Total assets"), tile(d, "LTP (last close, ₹)"), tile(d, "Debt / Equity")], ["25.50", "-", "-", "N/A"]);
  re(d, /Financial statements not loaded yet/);

  console.log("Detail view tests passed (" + checks + " checks)");
})().catch((x) => { console.error("DETAIL TEST FAILED:", x.message); process.exit(1); });
