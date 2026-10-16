// Run: node tests/test_coverage_labels.js   (no network, no browser)
// Universe-wide coverage fixes on the Stock Detail view: each section says whether a stock's data is not yet fetched, unavailable from the source,
// or when it was last updated; a missing value is "-", never 0. (The Company-name fallback to the stock directory is decision D3, a separate patch.)
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const code = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]).find((b) => b.includes("Stock Detail view"));
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const VAL = { as_of: "2026-10-16", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TATA CONSULTANCY SERV LT", pe: 15.82, pb: 6.89, roe: 43.37, roce: 53.06, roa: 27.18, ev_ebitda: 10.14, missing: [], fetched: "2026-10-14" },
  { symbol: "5PAISA", company_name: "5PAISA CAPITAL LIMITED", pe: 37.16, pb: 2.47, roe: 6.81, roce: null, roa: 2.32, ev_ebitda: 5.85, missing: ["roce"], fetched: "2026-10-15" }],
  errors: [{ symbol: "ABAN", error: "Symbol not found in Upstox NSE equity instruments" }, { symbol: "BADREQ", error: "key-ratios: HTTP 500" }] };
const FIN = { as_of: "2026-10-16", source: "Upstox", stocks: [], errors: [{ symbol: "ABAN", error: "Symbol not found in Upstox NSE equity instruments" }] };
const DIR = { symbols: [{ symbol: "POLYCAB", isin: "INE455K01017", name: "POLYCAB INDIA LIMITED" }, { symbol: "ABAN", isin: "INE421A01028", name: "ABAN OFFSHORE LTD" }, { symbol: "TCS", isin: "INE467B01029", name: "dir name" }] };
async function boot(hash) {
  const els = {}, win = {}, classes = new Set(), fetched = [];
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } },
    getElementById: (i) => els[i] || (els[i] = { id: i, innerHTML: "", textContent: "" }), addEventListener() {} };
  const files = { "fundamentals.json": VAL, "financials.json": FIN, "stock_directory.json": DIR, "historical.json": { stocks: {} }, "shareholding.json": { stocks: {} } };
  global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(code);
  await new Promise((r) => setTimeout(r, 40));
  const nav = async (h) => { window.location.hash = h; win.hashchange(); await new Promise((r) => setTimeout(r, 40)); };
  return { fetched, nav, detail: () => els.detail.innerHTML };
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tile = (h, label) => text((h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + label.replace(/[()/]/g, "\\$&") + "</span>")) || [])[1] || "NOT FOUND");
(async () => {
  let t = await boot("#stock=TCS"), d = t.detail();
  eq(tile(d, "Company"), "TATA CONSULTANCY SERV LT", "fundamentals name wins"); no(t.fetched.join(" "), /stock_directory/, "no directory request when a data file names the stock");
  re(d, /this stock: last updated 2026-10-14/, "fundamentals date of this stock");
  await t.nav("#stock=5PAISA"); d = t.detail();
  re(d, /not returned by the source: ROCE/, "a metric the source did not return is named"); eq(tile(d, "ROCE %"), "-", "and shown as '-', never 0");
  re(d, /this stock: not yet fetched/, "financial statements: not yet fetched (no row, no error)");
  re(d, /no price history has been fetched for this stock yet/, "price: says why there is no price");
  t = await boot("#stock=POLYCAB"); d = t.detail();
  eq(tile(d, "Company"), "-", "no data file names the stock: '-' (the directory fallback is decision D3)"); no(t.fetched.join(" "), /stock_directory/, "the page never reads the directory");
  re(d, /Fundamentals data: Upstox · Updated: 2026-10-16 · this stock: not yet fetched/, "fundamentals: not yet fetched");
  for (const k of ["P/E", "P/B", "ROE %", "Revenue", "Total assets", "Operating cash flow", "Latest Price (last close, ₹)", "Market Cap (₹ crore)"]) eq(tile(d, k), "-", k + " is '-' (never 0)");
  no(d, />0</, "no zero tile"); for (const bad of ["NaN", "undefined", "null"]) no(d, new RegExp(">" + bad + "<"), bad);
  await t.nav("#stock=ABAN"); d = t.detail();
  eq(tile(d, "Company"), "-"); re(d, /unavailable from source \(not in the Upstox equity list\)/, "provider does not list it");
  await t.nav("#stock=BADREQ"); d = t.detail();
  re(d, /not available \(the last request failed\)/, "a recorded failure is said as such"); eq(tile(d, "Company"), "-", "not in the directory: '-'");
  console.log("Coverage label tests passed (" + checks + " checks)");
})().catch((e) => { console.error("COVERAGE LABEL TEST FAILED:", e.message); process.exit(1); });
