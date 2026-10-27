"use strict";
// Run: node tests/test_screener_filters.js   (no network, no browser; stubs the DOM and fetch)
// The filter-first Fundamental screen in ../index.html (responsive redesign): P/E, P/B, ROE and Sector filters with strict numbers, the negative-P/E rule,
// sorting, the shared rule the other screen tabs use (window.SLScreen), and that the published values are shown unchanged. Read-only: nothing is written.
const fs = require("fs"), assert = require("assert"), vm = require("vm");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const START = "<script>\nlet FD=[],fsort={k:\"roe\",dir:-1};\n";
assert.ok(html.includes(START), "the screen script is in the page");
const code = "let FD=[],fsort={k:\"roe\",dir:-1};\n" + html.split(START)[1].split("</script>")[0];
let checks = 0;
const ok = (c, m) => { checks++; assert.ok(c, m); }, eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); };

const ROW = (symbol, o = {}) => Object.assign({ symbol, company_name: symbol + " Ltd", sector: "Alpha", pe: 10, pb: 2, roe: 20, roce: 15, roa: 5, ev_ebitda: 8 }, o);
const DATA = () => ({ as_of: "2026-10-01", source: "Fixture", stocks: [
  ROW("AAA", { pe: 8, pb: 1, roe: 30, sector: "Alpha" }),
  ROW("BBB", { pe: 25, pb: 4, roe: 16, sector: "Beta" }),
  ROW("CCC", { pe: -12, pb: 0.8, roe: -5, sector: "Alpha" }),            // loss-making: negative P/E
  ROW("DDD", { pe: null, pb: null, roe: null, sector: null }),           // nothing published
  ROW("EEE", { pe: "15", pb: "2", roe: "40", sector: "Beta" }),          // values stored as text: never pass a numeric filter
  ROW("FFF", { pe: 0, pb: 3, roe: 15, sector: "Gamma" }),                // zero P/E: neither positive nor negative
  ROW("GGG", { pe: 40, pb: -1, roe: 22, sector: "" }),                   // negative P/B, empty sector
  ROW("HHH", { pe: 20, pb: 2, roe: 15, company_name: "<b>H&H</b>", sector: "Beta" }),
  ROW("III", { pe: -0.5, pb: 1, roe: 1, sector: "Gamma" }),
  ROW("JJJ", { pe: 8, pb: 5, roe: 15, sector: "Alpha" })] });             // same P/E as AAA: ties keep the file's order

function boot(doc) {
  const els = {}, listeners = {};
  const el = (id, o = {}) => (els[id] = Object.assign({ id, value: "", hidden: false, disabled: false, textContent: "", innerHTML: "", attrs: {}, options: [],
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(t, f) { (listeners[id + ":" + t] = listeners[id + ":" + t] || []).push(f); },
    dispatchEvent(e) { (listeners[id + ":" + e.type] || []).forEach((f) => f(e)); }, click() { (listeners[id + ":click"] || []).forEach((f) => f({})); } }, o));
  ["fstat", "fund", "fsErr", "fsCount", "fsPeMin", "fsPeMax", "fsPbMin", "fsPbMax", "fsPeMode", "fsPbMode", "fsSector", "fsSectorNote", "fscreen", "fsReset"].forEach((i) => el(i));
  el("roe", { value: "15" }); els.fsPeMode.value = "any"; els.fsPbMode.value = "any";
  Object.defineProperty(els.fsSector, "innerHTML", { set(v) { this._h = v; this.options = [...v.matchAll(/<option value="([^"]*)">/g)].map((m) => m[1]); }, get() { return this._h; } });
  const ths = Array.from({ length: 9 }, () => el("th" + Math.random(), {}));
  const sortButtons = ["symbol", "company_name", "sector", "pe", "pb", "roe", "roce", "roa", "ev_ebitda"].map((k) => ({ dataset: { k }, onclick: null }));
  const events = [];
  const ctx = {
    document: { getElementById: (i) => els[i] || null, querySelectorAll: (q) => (q === "#ftable [data-k]" ? sortButtons : q === "#ftable th" ? ths : []),
      dispatchEvent: (e) => events.push(e.type) },
    window: {}, CustomEvent: function (t) { this.type = t; }, Event: function (t) { this.type = t; },
    fetch: async (u) => { ctx.fetched.push(u); return doc === undefined ? { ok: false } : { ok: true, json: async () => JSON.parse(JSON.stringify(doc)) }; },
    fetched: [], console, Object, String, Math, isFinite, JSON, Array, Number, Intl, setTimeout,
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  const t = {
    els, ctx, events, ths,
    ready: () => new Promise((r) => setTimeout(r, 20)),
    rows: () => [...els.fund.innerHTML.matchAll(/<tr><td>([^<]*)<\/td>/g)].map((m) => m[1]),
    msg: () => els.fund.innerHTML.replace(/<[^>]+>/g, ""),
    set(id, v) { els[id].value = v; els[id].dispatchEvent({ type: ["fsPeMin", "fsPeMax", "fsPbMin", "fsPbMax"].includes(id) ? "input" : "change" }); },
    sort(k) { sortButtons.find((b) => b.dataset.k === k).onclick(); },
    S: () => ctx.window.SLScreen,
  };
  return t;
}

(async () => {
  let t = boot(DATA()); await t.ready();
  // --- loading, coverage and the default (the existing ROE >= 15 default is kept)
  eq(t.ctx.fetched, ["out/fundamentals.json"], "reads only out/fundamentals.json");
  re(t.els.fstat.textContent, /^Fundamentals data: Fixture · Updated: 2026-10-01$/, "status line unchanged");
  eq(t.rows(), ["AAA", "GGG", "BBB", "FFF", "HHH", "JJJ"], "default: ROE at least 15, strict numbers (EEE's text \"40\" does not pass), sorted by ROE, highest first; equal ROE keeps file order");
  re(t.els.fsCount.textContent, /^6 of 10 stocks match the filters\.$/, "the count says how many of all stocks match");
  t.set("roe", "0");
  eq(t.rows().length, 10, "no filter: every published stock is listed (coverage unchanged)");
  re(t.els.fsCount.textContent, /^All 10 stocks \(no filter applied\)\.$/);
  eq(t.S().active, false, "SLScreen: nothing active"); eq(t.S().pass("ZZZ"), true, "with no filter, the other tabs keep every row, even one without fundamentals");

  // --- P/E range: positive P/E only, strict, inclusive bounds
  t.set("fsPeMax", "20");
  eq(t.rows().sort(), ["AAA", "HHH", "JJJ"], "P/E max 20: only finite positive P/E up to 20 (not negative CCC/III, not zero FFF, not missing DDD, not text EEE)");
  t.set("fsPeMin", "8"); eq(t.rows().sort(), ["AAA", "HHH", "JJJ"], "bounds are inclusive at both ends (8 and 20)");
  t.set("fsPeMin", "8.01"); eq(t.rows().sort(), ["HHH"], "just above the lower bound excludes 8");
  t.set("fsPeMin", ""); t.set("fsPeMax", "");
  t.set("fsPeMode", "positive"); eq(t.rows().sort(), ["AAA", "BBB", "GGG", "HHH", "JJJ"], "Positive only: P/E > 0 (zero, negative, missing and text excluded)");
  t.set("fsPeMode", "negative"); eq(t.rows().sort(), ["CCC", "III"], "Negative only: the loss-making stocks, on request");
  ok(t.els.fsPeMin.disabled && t.els.fsPeMax.disabled, "the P/E range boxes are disabled while Negative only is chosen");
  t.els.fsPeMax.value = "5"; t.set("fsPeMode", "negative"); eq(t.rows().sort(), ["CCC", "III"], "a P/E range is not applied to negative P/E");
  t.els.fsPeMax.value = ""; t.set("fsPeMode", "any"); ok(!t.els.fsPeMin.disabled, "the boxes come back with Any");

  // --- strict input validation: anything that is not a plain non-negative number is an error, never silently "any"
  for (const bad of ["abc", "12a", "-5", "1e3", "Infinity", "NaN", "1,000", "12.", "+-3", "0x10", "₹10", "--"]) {
    t.set("fsPeMax", bad);
    ok(!t.els.fsErr.hidden && /P\/E maximum: enter a number of 0 or more/.test(t.els.fsErr.textContent), "'" + bad + "' is rejected with a message");
    eq(t.els.fsPeMax.getAttribute("aria-invalid"), "true", "'" + bad + "' marks the box invalid");
    eq(t.rows(), [], "'" + bad + "': no rows are shown"); re(t.msg(), /Fix the filter marked above to see the results\./);
    re(t.els.fsCount.textContent, /No results: a filter needs fixing\./);
    ok(t.S().error && t.S().pass("AAA") === false, "'" + bad + "': the other tabs show nothing either");
  }
  for (const good of [" 20 ", "20", "20.0", ".5", "0"]) { t.set("fsPeMax", good); ok(t.els.fsErr.hidden && t.els.fsPeMax.getAttribute("aria-invalid") === null, "'" + good + "' is accepted"); }
  t.set("fsPeMax", ""); t.set("fsPbMin", "x"); re(t.els.fsErr.textContent, /^P\/B minimum: enter a number/, "P/B boxes are validated the same way");
  t.set("fsPbMin", ""); t.set("fsPeMin", "30"); t.set("fsPeMax", "10"); re(t.els.fsErr.textContent, /P\/E: the minimum is higher than the maximum\./, "min above max is an error"); eq(t.rows(), []);
  t.set("fsPeMin", ""); t.set("fsPeMax", ""); ok(t.els.fsErr.hidden, "clearing the boxes clears the message");

  // --- P/B: the same rule as P/E. A range lists positive P/B only; negative book value only on request (missing and text never pass)
  t.set("fsPbMax", "2"); eq(t.rows().sort(), ["AAA", "CCC", "HHH", "III"], "P/B max 2: positive P/B only (GGG's negative P/B is not a cheap price; DDD missing and EEE text excluded)");
  t.set("fsPbMin", "1"); eq(t.rows().sort(), ["AAA", "HHH", "III"], "P/B 1 to 2, inclusive");
  t.set("fsPbMin", ""); t.set("fsPbMax", "");
  t.set("fsPbMode", "positive"); eq(t.rows().sort(), ["AAA", "BBB", "CCC", "FFF", "HHH", "III", "JJJ"], "P/B Positive only: every stock with P/B > 0 (not GGG's negative, DDD's missing or EEE's text)");
  t.set("fsPbMode", "negative"); eq(t.rows(), ["GGG"], "P/B Negative only: negative book value, on request");
  ok(t.els.fsPbMin.disabled && t.els.fsPbMax.disabled, "the P/B range boxes are disabled while Negative only is chosen");
  t.els.fsPbMax.value = "abc"; t.set("fsPbMode", "negative"); ok(t.els.fsErr.hidden, "a disabled box is not validated or applied"); eq(t.rows(), ["GGG"]);
  t.els.fsPbMax.value = ""; t.set("fsPbMode", "any"); ok(!t.els.fsPbMin.disabled, "the P/B boxes come back with Any"); eq(t.S().active, false, "back to no filter");

  // --- ROE: strict
  t.set("roe", "25"); eq(t.rows(), ["AAA"], "ROE at least 25 (EEE's text \"40\" does not pass)"); t.set("roe", "0");

  // --- Sector: exact match on the published value; options from the data
  eq(t.els.fsSector.options, ["", "Alpha", "Beta", "Gamma"], "sector options: All, then each published sector once, sorted (empty and missing sectors are not options)");
  re(t.els.fsSectorNote.textContent, /^8 of 10 stocks have a published sector; the others are left out when a sector is chosen\.$/, "the note says how many stocks have a sector");
  t.set("fsSector", "Beta"); eq(t.rows().sort(), ["BBB", "EEE", "HHH"], "Sector Beta");
  t.set("fsSector", "beta"); eq(t.rows(), [], "exact match only"); t.set("fsSector", "");
  t.set("fsSector", "Alpha"); t.set("fsPeMax", "10"); eq(t.rows().sort(), ["AAA", "JJJ"], "filters combine (AND)");
  eq(t.S().pass("AAA"), true, "SLScreen: a passing stock passes for the other tabs"); eq(t.S().pass("CCC"), false, "and a failing one fails");
  eq(t.S().pass("ZZZ"), false, "a stock with no fundamentals row never passes while a filter is on");
  ok(t.events.includes("slscreen"), "the other tabs are told when the filters change");
  t.els.fsReset.click();
  t.els.fsPbMode.value = "negative"; t.els.fsReset.click();
  eq([t.els.fsPeMax.value, t.els.fsSector.value, t.els.fsPeMode.value, t.els.fsPbMode.value, t.els.roe.value], ["", "", "any", "any", "0"], "Clear filters resets every filter"); eq(t.rows().length, 10, "and lists every stock again");

  // --- sorting: text A-Z first, numbers high-low first; missing and text values last in both directions; P/E losses after positive P/E
  t.sort("symbol"); eq(t.rows(), ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ"], "Stock: A-Z on first click");
  t.sort("symbol"); eq(t.rows()[0], "JJJ", "second click reverses");
  t.sort("pe"); eq(t.rows(), ["GGG", "BBB", "HHH", "AAA", "JJJ", "FFF", "III", "CCC", "DDD", "EEE"], "P/E high to low: positive first, then zero and negative, then missing and text");
  eq(t.ths[3].getAttribute("aria-sort"), "descending", "the sorted column says so (aria-sort)"); eq(t.ths[0].getAttribute("aria-sort"), null, "other columns do not");
  t.sort("pe"); eq(t.rows(), ["AAA", "JJJ", "HHH", "BBB", "GGG", "CCC", "III", "FFF", "DDD", "EEE"], "P/E low to high: the lowest POSITIVE P/E first; a loss never sorts as the cheapest; equal P/E keeps file order");
  eq(t.ths[3].getAttribute("aria-sort"), "ascending");
  t.sort("pb"); eq(t.rows().slice(-3), ["GGG", "DDD", "EEE"], "P/B high to low: negative P/B (GGG) after every positive P/B, then missing and text");
  t.sort("pb"); eq(t.rows().slice(0, 2), ["CCC", "AAA"], "P/B low to high: the lowest POSITIVE P/B first (0.8, then 1 in file order)"); eq(t.rows().slice(-3), ["GGG", "DDD", "EEE"], "and negative P/B is never first");
  t.sort("sector"); eq(t.rows().slice(-2).sort(), ["DDD", "GGG"], "missing or empty sector sorts last");

  // --- values are shown exactly as published (formatting only), names are escaped
  t.set("roe", "0"); t.sort("symbol"); t.sort("symbol"); t.sort("symbol");
  const hhh = (t.els.fund.innerHTML.match(/<tr><td>HHH<\/td>.*?<\/tr>/) || [""])[0];
  re(hhh, /<td>&lt;b&gt;H&amp;H&lt;\/b&gt;<\/td>/, "company names are escaped, never markup"); re(hhh, /<td class="n">20<\/td><td class="n">2<\/td><td class="n">15<\/td>/, "numbers are shown as published");
  const eee = (t.els.fund.innerHTML.match(/<tr><td>EEE<\/td>.*?<\/tr>/) || [""])[0];
  re(eee, /<td class="n">-<\/td><td class="n">-<\/td><td class="n">-<\/td>/, "a value that is not a number is shown as a dash, like a missing one");

  // --- the source rows are never changed
  { const d = DATA(); const t2 = boot(d); await t2.ready(); t2.set("fsPeMax", "20"); t2.sort("pe"); eq(d, DATA(), "the fetched data is not modified"); }

  // --- no data, empty data, no sector data
  t = boot(undefined); await t.ready(); re(t.els.fstat.textContent, /Fundamentals not loaded yet\./); re(t.msg(), /Fundamentals not loaded yet\./); eq(t.ctx.window.SLScreen, undefined, "without fundamentals the other tabs keep their own (unfiltered) behaviour");
  t = boot({ as_of: "x", source: "y", stocks: [] }); await t.ready(); re(t.els.fstat.textContent, /Fundamentals not loaded yet\./);
  t = boot({ as_of: "2026-10-01", source: "Fixture", stocks: [ROW("AAA", { sector: null }), null, ROW("BBB", { sector: null })] }); await t.ready();
  ok(t.els.fsSector.disabled, "no published sector: the sector filter is off"); re(t.els.fsSectorNote.textContent, /No sector is published in the fundamentals data yet/);
  t.set("roe", "0"); eq(t.rows(), ["AAA", "BBB"], "a null entry in the file is skipped, every real row is listed");

  // --- the markup: a labelled filter form above the results; the existing ROE select, its choices and its default are kept
  const sec = html.slice(html.indexOf('<section class="mk-sec" id="screener-sec">'), html.indexOf("</section>", html.indexOf('id="screener-sec"')));
  ok(sec.indexOf('<form id="fscreen"') > 0 && sec.indexOf('<form id="fscreen"') < sec.indexOf('id="ftable"'), "the filters come first, before the results");
  re(sec, /<select id="roe"><option value="0">Any<\/option><option value="15" selected>15%<\/option><option value="20">20%<\/option><option value="25">25%<\/option><\/select>/, "the ROE choices and default are unchanged");
  for (const id of ["fsPeMin", "fsPeMax", "fsPbMin", "fsPbMax", "fsPeMode", "roe", "fsSector"]) re(sec, new RegExp('<label for="' + id + '">'), id + " has a visible label");
  re(sec, /<p id="fsErr" class="fs-err" role="alert" hidden>/, "errors are announced"); re(sec, /<p id="fsCount" class="fs-count" role="status" aria-live="polite">/, "the count is announced");
  re(sec, /<option value="negative">Negative only \(loss-making\)<\/option>/, "negative P/E is a separate, explicit choice");
  re(sec, /A P\/E range lists positive P\/E only/, "the rule is explained next to the boxes");
  re(sec, /<option value="negative">Negative only \(negative book value\)<\/option>/, "negative P/B is a separate, explicit choice"); re(sec, /A P\/B range lists positive P\/B only/, "and explained");
  for (const id of ["fsPbMode"]) re(sec, new RegExp('<label for="' + id + '">'), id + " has a visible label");
  // Load more (behaviour checked in a real browser by tests/browser_screener_checks.py): 50 rows per table, then "Show 50 more"
  re(code, /const FPAGE=50,FLIM=\{\};/, "50 rows per screen table"); re(code, /function fpager\(table\)\{/, "a pager for every screen table");
  re(code, /rows\.forEach\(\(tr,i\)=>\{const h=i>=lim;if\(tr\.hidden!==h\)tr\.hidden=h\}\)/, "rows past the limit are hidden, never removed (sorting and links see every row)");
  re(code, /const frefilter=\(\)=>\{for\(const k in FLIM\)delete FLIM\[k\];fund\(\)\};/, "a filter change starts again at the first 50");
  re(html, /@container \(max-width:640px\)\{\n#screener-sec \.panel\.scroll\{overflow:visible/, "mobile cards are a container query on the screen itself");
  re(html, /#screener-sec td:before\{content:attr\(data-label\)/, "each card field shows its column name");
  re(code, /function flabel\(root\)\{/, "the column names are added to the cells from the table headers");
  re(html, /@container \(max-width:640px\)\{[^]*?#ftabs\{flex-wrap:wrap;overflow:visible\}/, "on a phone the screen tabs wrap, so no tab is hidden off-screen");
  re(html, /#screener-sec td\.n\{grid-column:auto;display:block;/, "numbers sit in a compact grid inside each card (label above value)");
  re(html, /#screener-sec tbody tr\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(min\(7rem,100%\),1fr\)\);/, "card number columns are at least 7rem, so a 320px phone still gets two columns");
  re(html, /#screener-sec td:first-child\{[^}]*min-height:44px/, "the stock link in each card is a 44px target");
  console.log("Screener filter tests passed (" + checks + " checks)");
})().catch((e) => { console.error("SCREENER FILTER TEST FAILED:", e && e.message ? e.message : e); if (e && e.stack) console.error(e.stack.split("\n").slice(1, 4).join("\n")); process.exit(1); });
