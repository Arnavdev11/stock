// Run: node tests/test_ui.js   (no network; stubs the browser DOM and fetch)
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const code = html.split("<script>").pop().split("</script>")[0];   // the Phase 1 tabs script (last script block)

const B = (a, l, e, r, per = "Mar 2025", basis = "consolidated") => ({ period: per, basis, total_assets: a, total_liabilities: l,
  total_equity: e, liabilities_to_equity: r, total_debt: null, debt_to_equity: null });
const I = (v, g, basis = "consolidated") => ({ period: "Mar 2026", basis, revenue: v, revenue_growth: g, operating_profit: 5000,
  operating_profit_growth: 2.5, net_profit: -300, net_profit_growth: -4, });
const CF = (f) => ({ period: "Mar 2026", basis: "consolidated", operating: 1500, investing: -900, financing: -200, capex: null, free_cash_flow: f });
const FIN = { as_of: "2026-09-30", source: "Upstox", notes: { total_equity: "Equity note." }, stocks: [
  { symbol: "AAA", company_name: "AAA LTD", basis: "consolidated", income: I(1086181, 10.53), balance_sheet: B(2000, 800, 1200, 0.67), cash_flow: CF(600) },
  { symbol: "BBB", company_name: "BBB LTD", basis: "mixed", income: I(null, null, "standalone"), balance_sheet: B(null, null, null, null, null, null), cash_flow: CF(null) },
  { symbol: "CCC", company_name: "CCC LTD", basis: "consolidated", income: I(900, -1.2), balance_sheet: B(1000, 400, 600, 0.67), cash_flow: CF(null) }] };
const VAL = { as_of: "2026-09-30", source: "Upstox", stocks: [{ symbol: "AAA", roe: 22 }, { symbol: "BBB", roe: 9 }, { symbol: "CCC", roe: 30 }] };

function run(files, roe) {
  return new Promise((resolve) => {
    const els = {}, handlers = {}, status = [{ textContent: "" }, { textContent: "" }];
    const mk = (id) => ({ id, innerHTML: "", textContent: "", hidden: false, value: roe, children: [], attrs: {},
      addEventListener(t, f) { handlers[id + ":" + t] = f; }, setAttribute(k, v) { this.attrs[k] = String(v); },
      appendChild(c) { this.children.push(c); } });
    global.document = { getElementById: (i) => els[i] || (els[i] = mk(i)), createElement: () => mk("btn"),
      addEventListener(t, f) { handlers["doc:" + t] = f; }, querySelectorAll: (q) => (q === ".finstat" ? status : []) };
    global.fetch = async (u) => { const f = files[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f)) }; };
    eval(code);
    setTimeout(() => resolve({ els, handlers, status }), 30);
  });
}
const order = (h) => [...h.matchAll(/<td class="">([A-Z]{3})<\/td>/g)].map((m) => m[1]).join(",");
const text = (h) => h.replace(/<br>/g, " | ").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const click = (r, t, k) => r.handlers["doc:click"]({ target: { closest: () => ({ dataset: { t, k } }) } });

(async () => {
  let r = await run({ "financials.json": FIN, "fundamentals.json": VAL }, "15");
  assert.deepStrictEqual(r.els.ftabs.children.map((b) => b.textContent), ["Valuation & Returns", "Financial Health", "Cash Flow & Growth"]);
  assert.strictEqual(r.els.ftab1.hidden, false); assert.strictEqual(r.els.ftab2.hidden, true);
  assert.match(r.status[0].textContent, /Upstox · ₹ crore · Updated: 2026-09-30/);
  assert.strictEqual(order(r.els.t2.innerHTML), "AAA,CCC", "ROE filter (15) keeps AAA and CCC only");
  r.els.roe.value = "0"; r.handlers["roe:change"]();
  assert.strictEqual(order(r.els.t2.innerHTML), "AAA,BBB,CCC");
  const all = text(r.els.t2.innerHTML + r.els.t3a.innerHTML + r.els.t3b.innerHTML);
  for (const bad of ["NaN", "undefined", "null", "Infinity"]) assert.ok(!all.includes(bad), "found " + bad);
  assert.match(text(r.els.t2.innerHTML), /BBB BBB LTD - - - - -/, "missing balance sheet shows dashes");
  assert.match(text(r.els.t3b.innerHTML), /10,86,181 \| \+10\.53%/, "Indian grouping + growth");
  assert.match(text(r.els.t3b.innerHTML), /standalone/, "standalone basis is labelled");
  assert.match(text(r.els.t3a.innerHTML), /BBB Mar 2026 1,500 -900 -200 -/, "no FCF -> dash");
  click(r, "t2", "total_assets"); assert.strictEqual(order(r.els.t2.innerHTML), "AAA,CCC,BBB", "desc, null last");
  click(r, "t2", "total_assets"); assert.strictEqual(order(r.els.t2.innerHTML), "CCC,AAA,BBB", "asc, null still last");
  click(r, "t2", "symbol"); assert.strictEqual(order(r.els.t2.innerHTML), "AAA,BBB,CCC");
  r = await run({ "fundamentals.json": VAL }, "15");
  assert.strictEqual(r.status[0].textContent, "Financial statements not loaded yet.");
  for (const id of ["t2", "t3a", "t3b"]) assert.match(r.els[id].innerHTML, /not loaded yet/);
  r = await run({ "financials.json": FIN }, "15");
  assert.match(r.status[0].textContent, /ROE filter needs valuation data/); assert.strictEqual(order(r.els.t2.innerHTML), "AAA,BBB,CCC");
  console.log("UI tests passed (9 checks)");
})().catch((e) => { console.error("UI TEST FAILED:", e.message); process.exit(1); });
