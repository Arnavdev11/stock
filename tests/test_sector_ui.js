// Run: node tests/test_sector_ui.js   (no network, no browser)
// Sector Architecture Phase 1: the Sector Dashboard blocks draw ONLY an approved, available, valid sector document.
// Anything else (no file, not approved, not available, damaged, non-finite) must show exactly "Sector data unavailable".
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const code = (html.split('<script type="module" id="stocklens-market">')[1] || "").split("</script>")[0];
ok(code.length > 5000, "the market module is found");
global.window = {}; global.document = { getElementById: () => null, addEventListener() {} }; global.fetch = () => { throw new Error("no fetch in this test"); };
(0, eval)(code);
const M = window.SLMarket, UNAV = '<p class="mk-na">Sector data unavailable</p>';

const sec = (key, label, n, adv, dec, unch, med) => ({ key, label, stock_count: n, counted: adv + dec + unch, advances: adv, declines: dec, unchanged: unch, breadth_pct: adv / (adv + dec + unch) * 100, median_1d_pct: med, mean_1d_pct: med, volume: 1000, volume_stock_count: n, eligible: true, ineligible_reason: null });
const good = () => ({ kind: "sectors", public_display_approved: true, available: true, as_of: "2026-10-05",
  sectors: [sec("b", "Bravo", 10, 6, 3, 1, 1.5), sec("a", "Alpha", 8, 2, 6, 0, -0.8), sec("c", "Charlie", 12, 7, 4, 1, 0.4),
    { key: "d", label: "Delta", stock_count: 2, counted: 2, advances: 1, declines: 1, unchanged: 0, breadth_pct: 50, median_1d_pct: null, mean_1d_pct: null, volume: 5, eligible: false, ineligible_reason: "small" }],
  leaders: [{ key: "b", label: "Bravo", median_1d_pct: 1.5, stock_count: 10 }, { key: "c", label: "Charlie", median_1d_pct: 0.4, stock_count: 12 }],
  laggards: [{ key: "a", label: "Alpha", median_1d_pct: -0.8, stock_count: 8 }] });
const m0 = M.model(null, null, null), ms = (d, y) => M.model(null, null, null, d, y);

// 1. nothing loaded / not approved / not available / damaged -> exactly the unavailable text
for (const id of ["mk-sectors", "mk-sectorlead"]) {
  eq(M.sectionHtml(id, m0, {}), UNAV, id + ": no file");
  eq(M.sectionHtml(id, ms(null), {}), UNAV, id + ": null file");
  for (const [name, mut] of [["not approved", (d) => { d.public_display_approved = false; }], ["approval missing", (d) => { delete d.public_display_approved; }], ["approval is the text true", (d) => { d.public_display_approved = "true"; }],
    ["not available", (d) => { d.available = false; }], ["available is a string", (d) => { d.available = "true"; }], ["wrong kind", (d) => { d.kind = "sector_stocks"; }], ["bad date", (d) => { d.as_of = "soon"; }],
    ["no sectors list", (d) => { d.sectors = null; }], ["a median is missing", (d) => { d.sectors[0].median_1d_pct = null; }], ["a count is text", (d) => { d.sectors[1].advances = "many"; }],
    ["infinite median", (d) => { d.sectors[0].median_1d_pct = Infinity; }], ["NaN count", (d) => { d.sectors[2].stock_count = NaN; }], ["a sector without a label", (d) => { delete d.sectors[0].label; }],
    ["no eligible sector", (d) => { d.sectors.forEach((s) => { s.eligible = false; }); }]]) {
    const d = good(); mut(d); eq(M.sectionHtml(id, ms(d), {}), UNAV, id + ": " + name);
  }
}
eq(M.sectorsOf(undefined), null, "undefined"); eq(M.sectorsOf("x"), null, "a string"); eq(M.sectorsOf([]), null, "an array");

// 2. an approved, available document draws the table, counts, performance, advances/declines, Top 5 / Bottom 5
const m = ms(good()), t = M.sectionHtml("mk-sectors", m, {});
re(t, /Sector performance, end of day, <b>2026-10-05<\/b>/, "labelled end of day, with its date");
re(t, /equal-weight median/, "the method is stated");
ok(t.indexOf("Alpha") < t.indexOf("Bravo") && t.indexOf("Bravo") < t.indexOf("Charlie"), "rows sorted by label, deterministic");
no(t, /Delta/, "an ineligible sector is not shown with a headline");
re(t, /data-mk-sector="b"/, "each row can open its stocks (drill-down hook)");
re(t, /<span class="up">\+1\.50%<\/span>/, "positive median is green"); re(t, /<span class="dn">-0\.80%<\/span>/, "negative median is red");
for (const h of ["Sector", "Stocks", "Median 1D", "Advances", "Declines", "Breadth"]) re(t, new RegExp("<th[^>]*>" + h + "</th>"), "column " + h);
const l = M.sectionHtml("mk-sectorlead", m, {});
re(l, /Sector Leaders[\s\S]*Bravo[\s\S]*Charlie[\s\S]*Sector Laggards[\s\S]*Alpha/, "leaders then laggards");
re(l, /Top 5 by median 1-day change/, "Top 5 label"); re(l, /Bottom 5 by median 1-day change/, "Bottom 5 label");

// 3. drill-down: stocks come only from an approved sector_stocks document
const stocks = () => ({ kind: "sector_stocks", public_display_approved: true, sectors: { b: { label: "Bravo", members: [
  { symbol: "S1", close: 10, change_pct: 3, volume: 1 }, { symbol: "S2", close: 20, change_pct: -2, volume: 1 }, { symbol: "S3", close: 30, change_pct: 1, volume: 1 },
  { symbol: "S4", close: 40, change_pct: 0, volume: 1 }, { symbol: "S5", close: 50, change_pct: 5, volume: 1 }, { symbol: "S6", close: 60, change_pct: -7, volume: 1 }, { symbol: "S7", close: 70, change_pct: 2, volume: 1 }] } } });
const st = M.stocksOf(stocks(), "b"); eq(st.map((x) => x.symbol), ["S5", "S1", "S7", "S3", "S4", "S2", "S6"], "stocks sorted by change, best first");
eq(M.stocksOf(stocks(), "zzz"), null, "unknown sector"); const bad = stocks(); bad.public_display_approved = false; eq(M.stocksOf(bad, "b"), null, "not approved");
const withStocks = ms(good(), stocks()), dd = M.sectionHtml("mk-sectors", withStocks, { sector: "b" });
re(dd, /Top 5 in sector[\s\S]*S5[\s\S]*S1[\s\S]*S7[\s\S]*S3[\s\S]*S4/, "Top 5 in the sector"); re(dd, /Bottom 5 in sector[\s\S]*S6[\s\S]*S2[\s\S]*S4/, "Bottom 5 in the sector");
re(M.sectionHtml("mk-sectors", ms(good(), false), { sector: "b" }), /<div class="mk-card"><p class="mk-na">Sector data unavailable<\/p><\/div>/, "stocks file failed to load: unavailable, table kept");
no(M.sectionHtml("mk-sectors", ms(good(), bad), { sector: "b" }).split("Sector performance")[1].split("</table>")[1], /S5/, "an unapproved stocks file is not drawn");

// 4. wording and safety of the added code
const added = code.slice(code.indexOf("function sna()"), code.indexOf('S_["mk-movers"]'));
ok(added.length > 1000, "the sector code is found");
no(added + t + l + dd, /\bBuy\b|\bSell\b|\blive\b|recommend|Target|Rating|licen[sc]|NSE|Upstox|permitted|approved by/i, "no advice, live or licensing words in the sector blocks");
no(code, /sector_master|etf_list|private\//, "the page never mentions the private sector master or folder");
eq((code.match(/getJson\("out\/market_sector[^"]*"\)/g) || []).length, 2, "exactly two sector files are requested, both under out/ (they exist there only when approved)");
console.log("Sector UI tests passed (" + checks + " checks)");
