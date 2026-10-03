
// Run: node tests/test_ui.js   (no network, no browser; stubs the DOM and fetch)
// Tests the Phase 2B Financial Health and Cash Flow & Growth tabs in ../index.html. Read-only: nothing is written.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const code = html.split("<script>").pop().split("</script>")[0];       // the Fundamental tabs script (last script block)
let checks = 0;
const ok = (c, m) => { checks++; assert.ok(c, m); }, eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };

const INC = (i, o = {}) => Object.assign({ period: "Mar 2025", basis: "consolidated", revenue: 1000 * i, other_income: 10 * i, total_revenue: 1010 * i,
  profit_before_tax: 200 * i, tax: 50 * i, profit_after_tax: 150 * i, eps_basic: 1.5 * i, eps_diluted: 1.4 * i, total_revenue_growth: 5 + i,
  profit_before_tax_growth: -2.5, profit_after_tax_growth: 3, net_profit: 150 * i, net_profit_growth: 3, checks: {} }, o);
const BAL = (i, o = {}) => Object.assign({ period: "Mar 2025", basis: "consolidated", total_assets: 5000 * i, total_liabilities: 3000 * i,
  total_equity: 2000 * i, liabilities_to_equity: 1.5, total_debt: null, debt_to_equity: null, debt_lines_found: [], line_items: [], checks: {} }, o);
const CF = (i, o = {}) => Object.assign({ period: "Mar 2025", basis: "consolidated", operating: 400 * i, investing: -300 * i, financing: -50 * i,
  capex: null, capex_line: null, free_cash_flow: null, capex_candidates: [], capex_status: "none", line_items: ["x"] }, o);
const stock = (n, i, o = {}) => Object.assign({ symbol: n, isin: "INE" + n, company_name: n + " LTD", basis: "consolidated",
  income: INC(i), balance_sheet: BAL(i), cash_flow: CF(i) }, o);
const FIN = { schema: 2, as_of: "2026-10-01", source: "Upstox", notes: {}, errors: [], stocks: [
  stock("AAA", 1), stock("BBB", 2, { income: INC(2, { revenue: null, eps_diluted: null, profit_before_tax_growth: null }),
                                    balance_sheet: BAL(2, { total_assets: null, total_liabilities: null, total_equity: null, liabilities_to_equity: null }) }),
  stock("CCC", 3, { cash_flow: CF(3, { financing: null }), balance_sheet: BAL(3, { liabilities_to_equity: 11.2 }) })] };
const VAL = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "AAA", company_name: "AAA LTD", roe: 22, roce: 15, roa: 5, pe: 12, pb: 2, ev_ebitda: 8 },
  { symbol: "BBB", company_name: "BBB LTD", roe: 9, roce: null, roa: 3, pe: null, pb: 1, ev_ebitda: null },
  { symbol: "CCC", company_name: "CCC LTD", roe: 30, roce: 18, roa: 7, pe: 20, pb: 4, ev_ebitda: 10 }] };

async function run(files, roe) {
  const els = {}, handlers = {}, status = [{ textContent: "" }, { textContent: "" }];
  const mk = (id) => ({ id, innerHTML: "", textContent: "", hidden: false, value: roe, children: [], attrs: {},
    addEventListener(t, f) { handlers[id + ":" + t] = f; }, setAttribute(k, v) { this.attrs[k] = String(v); }, appendChild(c) { this.children.push(c); } });
  global.document = { getElementById: (i) => els[i] || (els[i] = mk(i)), createElement: () => mk("btn"),
    addEventListener(t, f) { handlers["doc:" + t] = f; }, querySelectorAll: (q) => (q === ".finstat" ? status : []) };
  global.fetch = async (u) => { const f = files[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f)) }; };
  eval(code);
  await new Promise((r) => setTimeout(r, 30));
  return { els, handlers, status };
}
const text = (h) => h.replace(/<br>/g, " | ").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const heads = (h) => [...h.matchAll(/<button class="lk"[^>]*>([^<]+)<\/button>/g)].map((m) => m[1]);
const order = (h) => [...h.matchAll(/<td class="">([A-Z]{3})<\/td>/g)].map((m) => m[1]).join(",");
const row = (h, s) => text((h.match(new RegExp('<tr><td class="">' + s + "</td>[\\s\\S]*?</tr>")) || [""])[0]);
const click = (r, t, k) => r.handlers["doc:click"]({ target: { closest: () => ({ dataset: { t, k } }) } });
const setRoe = (r, v) => { r.els.roe.value = v; r.handlers["roe:change"](); };
const IDS = ["t2a", "t2b", "t2", "t3a", "t3b"];

(async () => {
  let r = await run({ "financials.json": FIN, "fundamentals.json": VAL }, "0"), e = r.els;
  // --- tabs and status
  eq(e.ftabs.children.map((b) => b.textContent), ["Valuation & Returns", "Financial Health", "Cash Flow & Growth"]);
  ok(e.ftab1.hidden === false && e.ftab2.hidden === true && e.ftab3.hidden === true, "Valuation tab is the default");
  re(r.status[0].textContent, /^Financial data: Upstox · ₹ crore · Updated: 2026-10-01$/);
  re(e.vstat.textContent, /Fundamentals data: Upstox · Updated: 2026-10-01/);
  // --- columns: the current UI, and nothing from the old one
  eq(heads(e.t2a.innerHTML), ["Stock", "Period", "Revenue (₹ cr)", "Total revenue (₹ cr)", "Profit before tax (₹ cr)", "Profit after tax (₹ cr)", "EPS basic (₹)", "EPS diluted (₹)"]);
  eq(heads(e.t2b.innerHTML), ["Stock", "ROE %", "ROCE %", "ROA %", "P/E", "P/B", "EV/EBITDA"]);
  eq(heads(e.t2.innerHTML), ["Stock", "Company", "Period", "Total assets (₹ cr)", "Total liabilities (₹ cr)", "Total equity (₹ cr)", "Liabilities / Equity", "Debt / Equity"]);
  eq(heads(e.t3a.innerHTML), ["Stock", "Period", "Operating cash flow (₹ cr)", "Investing cash flow (₹ cr)", "Financing cash flow (₹ cr)", "Free cash flow (₹ cr)"]);
  eq(heads(e.t3b.innerHTML), ["Stock", "Period", "Total revenue (₹ cr) + growth %", "Profit before tax (₹ cr) + growth %", "Profit after tax (₹ cr) + growth %"]);
  const everything = IDS.map((i) => e[i].innerHTML).join(" "), flat = text(everything);
  no(flat, /operating profit/i, "no Operating Profit column or value");
  const allHeads = IDS.flatMap((i) => heads(e[i].innerHTML));
  ok(!allHeads.some((h) => /^Revenue \(₹ cr\) \+ growth/.test(h)), "no old Revenue + growth column");
  ok(!allHeads.some((h) => /operating profit/i.test(h)), "no Operating Profit header");
  for (const bad of ["NaN", "undefined", "null", "Infinity"]) ok(!flat.includes(bad), "found " + bad);
  // --- values come from the right fields
  re(row(e.t2a.innerHTML, "AAA"), /AAA Mar 2025 1,000 1,010 200 150 1\.50 1\.40$/);       // revenue, total_revenue, PBT, PAT, EPS b/d
  re(row(e.t2b.innerHTML, "AAA"), /AAA 22\.00 15\.00 5\.00 12\.00 2\.00 8\.00$/);          // ROE ROCE ROA P/E P/B EV/EBITDA
  re(row(e.t2.innerHTML, "CCC"), /CCC CCC LTD Mar 2025 15,000 9,000 6,000 11\.20 N\/A$/);  // Liabilities / Equity from liabilities_to_equity
  re(row(e.t3b.innerHTML, "AAA"), /AAA Mar 2025 1,010 \| \+6\.00% 200 \| -2\.50% 150 \| \+3\.00%$/);
  // --- Debt / Equity is N/A everywhere; never filled from liabilities / equity
  for (const s of ["AAA", "BBB", "CCC"]) re(row(e.t2.innerHTML, s), /N\/A$/, "Debt / Equity N/A for " + s);
  no(row(e.t2.innerHTML, "AAA"), /1\.50 1\.50/, "Liabilities / Equity is not repeated as Debt / Equity");
  // --- Free cash flow is N/A everywhere; never operating + investing
  for (const s of ["AAA", "BBB", "CCC"]) re(row(e.t3a.innerHTML, s), /N\/A$/, "FCF N/A for " + s);
  no(row(e.t3a.innerHTML, "AAA"), /\b100\b/, "AAA: 400 + -300 is not shown as FCF");
  eq(e.fcfnote.textContent, "Free Cash Flow is unavailable because Upstox did not provide an unambiguous capital-expenditure line for these stocks.");
  re(e.n2.textContent, /not Debt \/ Equity/); re(e.n2.textContent, /customer deposits/); re(e.n2.textContent, /Debt \/ Equity is N\/A/);
  // --- missing values are "-"
  re(row(e.t2a.innerHTML, "BBB"), /BBB Mar 2025 - 2,020 400 300 3\.00 -$/);               // revenue and EPS diluted missing
  re(row(e.t2b.innerHTML, "BBB"), /BBB 9\.00 - 3\.00 - 1\.00 -$/);                         // ROCE, P/E, EV/EBITDA missing
  re(row(e.t2.innerHTML, "BBB"), /BBB BBB LTD Mar 2025 - - - - N\/A$/);                    // balance sheet missing; D/E still N/A
  re(row(e.t3a.innerHTML, "CCC"), /CCC Mar 2025 1,200 -900 - N\/A$/);                      // financing missing
  re(row(e.t3b.innerHTML, "BBB"), /BBB Mar 2025 2,020 \| \+7\.00% 400 \| - 300 \| \+3\.00%$/);  // PBT growth missing
  // --- ROE filter applies to every table
  setRoe(r, "15");  for (const i of IDS) eq(order(e[i].innerHTML), "AAA,CCC", "ROE>=15 in " + i);
  setRoe(r, "25");  for (const i of IDS) eq(order(e[i].innerHTML), "CCC", "ROE>=25 in " + i);
  setRoe(r, "40");  re(text(e.t2a.innerHTML), /No stocks match/);
  setRoe(r, "0");   for (const i of IDS) eq(order(e[i].innerHTML), "AAA,BBB,CCC", "ROE any in " + i);
  // --- sorting: numbers both ways, text, and missing values always last
  click(r, "t2a", "revenue");   eq(order(e.t2a.innerHTML), "CCC,AAA,BBB", "revenue desc, missing last");
  click(r, "t2a", "revenue");   eq(order(e.t2a.innerHTML), "AAA,CCC,BBB", "revenue asc, missing still last");
  click(r, "t2a", "symbol");    eq(order(e.t2a.innerHTML), "AAA,BBB,CCC");
  click(r, "t2b", "pe");        eq(order(e.t2b.innerHTML), "CCC,AAA,BBB", "P/E desc, missing last");
  click(r, "t2", "liabilities_to_equity"); eq(order(e.t2.innerHTML), "CCC,AAA,BBB", "Liabilities / Equity sort");
  click(r, "t3a", "operating"); eq(order(e.t3a.innerHTML), "CCC,BBB,AAA", "operating cash flow desc");
  // --- a value present for one stock changes the note, never the arithmetic
  const withFcf = JSON.parse(JSON.stringify(FIN)); withFcf.stocks[0].cash_flow.free_cash_flow = 90;
  r = await run({ "financials.json": withFcf, "fundamentals.json": VAL }, "0");
  re(r.els.fcfnote.textContent, /shown only where Upstox provided an unambiguous capital-expenditure line/);
  re(row(r.els.t3a.innerHTML, "AAA"), /AAA Mar 2025 400 -300 -50 90$/); re(row(r.els.t3a.innerHTML, "BBB"), /N\/A$/);
  // --- each file fails independently
  r = await run({ "fundamentals.json": VAL }, "15");
  eq(r.status[0].textContent, "Financial statements not loaded yet.");
  for (const i of ["t2a", "t2", "t3a", "t3b"]) re(r.els[i].innerHTML, /Financial statements not loaded yet/);
  eq(order(r.els.t2b.innerHTML), "AAA,CCC", "returns table still renders");
  r = await run({ "financials.json": FIN }, "15");
  re(r.status[0].textContent, /ROE filter needs valuation data/); re(r.els.t2b.innerHTML, /Fundamentals not loaded yet/);
  eq(order(r.els.t2a.innerHTML), "AAA,BBB,CCC", "ROE filter is off without valuation data");
  r = await run({}, "15");
  for (const i of IDS) re(r.els[i].innerHTML, /not loaded yet/);
  console.log("UI tests passed (" + checks + " checks)");
})().catch((x) => { console.error("UI TEST FAILED:", x.message); process.exit(1); });
