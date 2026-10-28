"use strict";
// Run: node tests/test_homepage_fixes.js   (no network, no browser; stubs the DOM and fetch)
// Regression tests for the homepage review fixes in the Fundamental screen (index.html):
//   * a P/E, P/B or EV/EBITDA of exactly 0 is shown as an em dash and sorted with the missing values (0 cannot be a real value of a ratio to a price or an
//     enterprise value); a real 0 for ROE, ROCE or ROA is kept;
//   * an ROE over negative shareholders' equity (negative P/B, or a loss with a positive ROE) is never ranked as a high return: it is left out of the ROE
//     filter, sorted after every comparable ROE and flagged;
//   * the Sector column is hidden while fewer than half of the stocks have a published sector; nothing is filled in;
//   * numbers are right-aligned and stock symbols look like the links in the other tables; dates read "01 Oct 2026"; a negative number has a true minus sign.
// The ROE rows below are REAL published values, copied from a screenshot of the live Fundamental screen (28 Oct 2026); the rest are invented fixtures.
const fs = require("fs"), assert = require("assert"), vm = require("vm");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
let checks = 0;
const ok = (c, m) => { checks++; assert.ok(c, m); }, eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const START = "<script>\nlet FD=[],fsort={k:\"roe\",dir:-1};\n";
ok(html.includes(START), "the screen script is in the page");
const code = "let FD=[],fsort={k:\"roe\",dir:-1};\n" + html.split(START)[1].split("</script>")[0];

// real rows (screenshot of the live screen) -------------------------------------------------------------------------------------------
const REAL = [
  ["SHIVACEM", -3.21, -5.82, 571.79, -1.47, -6.09, 52.94], ["MCCHRLS-B", -7.6, -74.67, 568.9, 4.66, -7.26, 45.63], ["GLOBALVECT", -4.46, -22.86, 398.47, -1.65, -3.91, 8.98],
  ["VIVIDHA", -10, 0, 377.5, -20.13, -22.13, -39.92], ["ROML", 15.07, 12.42, 216.77, 19.41, 10.27, 13.53], ["SHIVAMAUTO", -3.02, -3.48, 199.92, -1.27, -18.06, 18.08],
  ["COLPAL", 23.68, 27.81, 158.62, 179.72, 129.66, 16.6], ["PRAXIS", -1.11, -1.35, 86.34, -40.51, -32.97, -12.65], ["OMAXE", -3.76, -1.08, 77.3, -64.76, -3.09, -5.4],
  ["IDEA", 3.29, -2.58, 67.24, -1.58, 19.52, 3.86], ["ARDEE", 0, 2.98, 57.46, 41.28, 25.43, 0], ["NIBL", -2.48, -0.52, 53.45, -20.17, -26.14, -15.46],
  ["NITYAS", 0, 0, 53.28, 49.34, 22.07, 0], ["AUGMONT", 0, 0, 53.18, 70.24, 35.03, 0], ["PAGEIND", 54.08, 27.2, 50.83, 60.14, 26.46, 35.18]]
  .map(([symbol, pe, pb, roe, roce, roa, ev_ebitda]) => ({ symbol, company_name: symbol + " LTD", sector: null, pe, pb, roe, roce, roa, ev_ebitda }));
const NOT_COMPARABLE = ["SHIVACEM", "MCCHRLS-B", "GLOBALVECT", "VIVIDHA", "SHIVAMAUTO", "PRAXIS", "OMAXE", "IDEA", "NIBL"];   // P/B < 0, or P/E < 0 with ROE > 0

function boot(doc) {
  const els = {}, listeners = {};
  const el = (id, o = {}) => (els[id] = Object.assign({ id, value: "", hidden: false, disabled: false, textContent: "", innerHTML: "", attrs: {}, options: [],
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(t, f) { (listeners[id + ":" + t] = listeners[id + ":" + t] || []).push(f); },
    dispatchEvent(e) { (listeners[id + ":" + e.type] || []).forEach((f) => f(e)); }, click() { (listeners[id + ":click"] || []).forEach((f) => f({})); } }, o));
  ["fstat", "fund", "fsErr", "fsCount", "fsPeMin", "fsPeMax", "fsPbMin", "fsPbMax", "fsPeMode", "fsPbMode", "fsSector", "fsSectorNote", "fscreen", "fsReset", "fsRoeNote"].forEach((i) => el(i));
  const cls = new Set(); el("ftable", { classList: { toggle: (c, on) => { if (on) cls.add(c); else cls.delete(c); return on; }, contains: (c) => cls.has(c) } });
  el("roe", { value: "15" }); els.fsPeMode.value = "any"; els.fsPbMode.value = "any";
  Object.defineProperty(els.fsSector, "innerHTML", { set(v) { this._h = v; this.options = [...v.matchAll(/<option value="([^"]*)">/g)].map((m) => m[1]); }, get() { return this._h; } });
  const ths = Array.from({ length: 9 }, () => el("th" + Math.random(), {}));
  const sortButtons = ["symbol", "company_name", "sector", "pe", "pb", "roe", "roce", "roa", "ev_ebitda"].map((k) => ({ dataset: { k }, onclick: null }));
  const ctx = { document: { getElementById: (i) => els[i] || null, querySelectorAll: (q) => (q === "#ftable [data-k]" ? sortButtons : q === "#ftable th" ? ths : []), dispatchEvent() {} },
    window: {}, CustomEvent: function (t) { this.type = t; }, Event: function (t) { this.type = t; },
    fetch: async () => ({ ok: true, json: async () => JSON.parse(JSON.stringify(doc)) }), console, Object, String, Math, isFinite, JSON, Array, Number, Intl, setTimeout };
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return { els, ctx, cls, ready: () => new Promise((r) => setTimeout(r, 20)),
    rows: () => [...els.fund.innerHTML.matchAll(/<tr><td>([^<]*)<\/td>/g)].map((m) => m[1]),
    row: (s) => (els.fund.innerHTML.match(new RegExp("<tr><td>" + s + "</td>.*?</tr>")) || [""])[0],
    cells: (s) => [...((els.fund.innerHTML.match(new RegExp("<tr><td>" + s + "</td>.*?</tr>")) || [""])[0]).matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((m) => m[1].replace(/<span class="mk-vh">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, "")),
    set(id, v) { els[id].value = v; els[id].dispatchEvent({ type: ["fsPeMin", "fsPeMax", "fsPbMin", "fsPbMax"].includes(id) ? "input" : "change" }); },
    sort(k) { sortButtons.find((b) => b.dataset.k === k).onclick(); }, S: () => ctx.window.SLScreen, F: (n) => ctx[n] || vm.runInContext(n, ctx) };
}

(async () => {
  const doc = { as_of: "2026-10-01", source: "Fixture", stocks: REAL.concat([
    { symbol: "ZROE", company_name: "Zero Return Ltd", sector: null, pe: 12, pb: 1.5, roe: 0, roce: 0, roa: 0, ev_ebitda: 6 },          // a genuine 0 ROE / ROCE / ROA
    { symbol: "LOSSNEG", company_name: "Loss Negative Ltd", sector: null, pe: -8, pb: 1.2, roe: -14, roce: -9, roa: -3, ev_ebitda: -20 }]) };   // a loss on positive equity: a real negative ROE
  const t = boot(doc); await t.ready();
  const froeBad = t.F("froeBad");

  // ---- 1. ROE over negative equity is detected from the published values themselves
  eq(REAL.filter((x) => froeBad(x)).map((x) => x.symbol), NOT_COMPARABLE, "every real row with a negative P/B, or a loss with a positive ROE, is detected; no other row");
  eq([froeBad({ roe: 20, pb: -1, pe: 5 }), froeBad({ roe: 20, pb: 2, pe: -5 }), froeBad({ roe: -20, pb: 2, pe: -5 }), froeBad({ roe: 20, pb: null, pe: 5 }), froeBad({ roe: null, pb: -1 }), froeBad({ roe: 20, pb: "-1", pe: 5 })],
     [true, true, false, false, false, false], "the rule: negative P/B, or negative P/E with positive ROE; a loss with a negative ROE is a real figure; missing or text values decide nothing");

  // ---- 2. the default ROE >= 15 ranking no longer opens on companies with negative equity
  eq(t.rows(), ["ROML", "COLPAL", "ARDEE", "NITYAS", "AUGMONT", "PAGEIND"], "default (ROE at least 15, highest first): only comparable ROEs, so ROML and COLPAL lead instead of SHIVACEM (571.79%) and MCCHRLS-B (568.9%)");
  for (const s of NOT_COMPARABLE) eq(t.S().pass(s), false, s + " does not pass the ROE filter in the other screen tabs either (window.SLScreen)");
  t.set("roe", "0");
  const all = t.rows(), firstBad = all.indexOf("SHIVACEM"), lastGood = Math.max(...["ROML", "COLPAL", "ARDEE", "NITYAS", "AUGMONT", "PAGEIND", "ZROE", "LOSSNEG"].map((s) => all.indexOf(s)));
  ok(firstBad > lastGood, "ROE any, highest first: every comparable ROE (including the 0 and the negative one) comes before any ROE that is not comparable");
  eq(all.slice(firstBad), NOT_COMPARABLE, "and the ones that are not comparable follow, by their published ROE");
  t.sort("roe"); const asc = t.rows(); ok(asc.indexOf("LOSSNEG") === 0 && asc.indexOf("SHIVACEM") > asc.indexOf("ROML"), "lowest first: the real negative ROE leads; a not-comparable ROE is still never ranked among the comparable ones");
  t.sort("roe");
  for (const s of NOT_COMPARABLE) { re(t.row(s), /<td class="n">[^<]*<span class="fs-flag" title="Not comparable: negative shareholders\u2019 equity, or a loss alongside a positive ROE">\u2020<span class="mk-vh"> not comparable<\/span><\/span><\/td>/, s + ": flagged in its ROE cell, with text for screen readers"); }
  eq(t.cells("SHIVACEM")[5], "571.79\u2020", "the value itself is still shown as published, with the flag");
  for (const s of ["ROML", "COLPAL", "ZROE", "LOSSNEG"]) no(t.row(s), /fs-flag/, s + ": a comparable ROE is not flagged");
  eq(t.els.fsRoeNote.hidden, false, "the note under the table is shown"); re(t.els.fsRoeNote.textContent, /^\u2020 9 of the stocks listed have an ROE that is not comparable \(negative shareholders\u2019 equity, or a loss alongside a positive ROE\)\. It is shown as published, but sorted last and left out of the ROE filter\.$/, "and counts them");
  t.set("roe", "15"); eq(t.els.fsRoeNote.hidden, true, "with the default filter none is listed, so the note is hidden");

  // ---- 3. a P/E, P/B or EV/EBITDA of exactly 0 is not a value; a real 0 ROE / ROCE / ROA is kept
  t.set("roe", "0");
  eq(t.cells("NITYAS").slice(3), ["\u2014", "\u2014", "53.28", "49.34", "22.07", "\u2014"], "NITYAS (real row): P/E 0, P/B 0 and EV/EBITDA 0 are shown as dashes, never as 0");
  eq(t.cells("ARDEE").slice(3), ["\u2014", "2.98", "57.46", "41.28", "25.43", "\u2014"], "ARDEE (real row): P/E 0 and EV/EBITDA 0 are dashes, its real P/B stays");
  eq(t.cells("VIVIDHA").slice(3, 5), ["\u221210", "\u2014"], "VIVIDHA (real row): P/B 0 is a dash; the negative P/E carries a true minus sign");
  eq(t.cells("ZROE").slice(5), ["0", "0", "0", "6"], "a real ROE, ROCE and ROA of 0 is shown as 0");
  eq(t.cells("LOSSNEG").slice(3), ["\u22128", "1.2", "\u221214", "\u22129", "\u22123", "\u221220"], "negative values carry a true minus sign (U+2212), never a hyphen");
  no(t.els.fund.innerHTML.replace(/<[^>]+>/g, " "), /(^|\s)-\d/, "no hyphen is used as a minus sign anywhere in the table");
  t.sort("ev_ebitda"); const ev = t.rows(); eq(ev.slice(-3).sort(), ["ARDEE", "AUGMONT", "NITYAS"], "sorting by EV/EBITDA puts a 0 with the missing values, at the end"); t.sort("ev_ebitda");
  t.sort("pe"); t.sort("pe"); const pe = t.rows(); eq(pe.slice(-3).sort(), ["ARDEE", "AUGMONT", "NITYAS"], "P/E lowest first: a P/E of 0 is never shown as the cheapest stock");
  t.set("fsPeMode", "positive"); for (const s of ["NITYAS", "ARDEE", "AUGMONT"]) ok(!t.rows().includes(s), s + ": a P/E of 0 is not a positive P/E"); t.set("fsPeMode", "any");

  // ---- 4. the Sector column: hidden while it would be a column of dashes; never filled in
  ok(t.cls.has("fs-nosector"), "no stock has a published sector: the column is hidden");
  const few = boot({ as_of: "2026-10-01", source: "F", stocks: Array.from({ length: 20 }, (_, i) => ({ symbol: "S" + i, pe: 1, pb: 1, roe: 20, sector: i < 2 ? "Alpha" : null })) }); await few.ready();
  ok(few.cls.has("fs-nosector"), "2 of 20 (like 10 of 2,353 on the live site): hidden"); re(few.els.fsSectorNote.textContent, /^2 of 20 stocks have a published sector/, "and the filter's note still says how many have one");
  re(few.row("S0"), /<td>Alpha<\/td>/, "the published sector is still in the row (only the column is hidden)"); re(few.row("S5"), /<td>\u2014<\/td>/, "a missing sector stays a dash, never invented");
  const most = boot({ as_of: "2026-10-01", source: "F", stocks: Array.from({ length: 20 }, (_, i) => ({ symbol: "S" + i, pe: 1, pb: 1, roe: 20, sector: i < 12 ? "Alpha" : null })) }); await most.ready();
  ok(!most.cls.has("fs-nosector"), "12 of 20: the column is shown");
  re(html, /#ftable\.fs-nosector th:nth-child\(3\),#ftable\.fs-nosector td:nth-child\(3\)\{display:none\}/, "the column is hidden by CSS only"); re(code, /const FSECTOR_MIN=0\.5;/, "the threshold is half of the stocks");

  // ---- 5. alignment, links, dates
  re(html, /<th class="n"><button class="lk" data-k="pe">P\/E<\/button><\/th>/, "numeric headers carry the number class"); eq((html.match(/<th class="n"><button class="lk" data-k="(pe|pb|roe|roce|roa|ev_ebitda)">/g) || []).length, 6, "all six numeric columns");
  re(html, /#screener-sec td\.n,#screener-sec th\.n,#screener-sec th\.n \.lk\{text-align:right\}/, "numbers and their headers are right-aligned, like every other table");
  re(html, /#ftable td:first-child,#t2a td:first-child[^{]*\{cursor:pointer;color:var\(--ac\);font-weight:700;text-decoration:none;white-space:nowrap\}/, "symbols are bold accent links without an underline, like the dashboard tables");
  re(html, /#screener-sec th \.lk\{text-align:left;text-decoration:none\}#screener-sec th \.lk:hover\{text-decoration:underline\}/, "sort headers are not underlined until hovered");
  re(t.els.fstat.textContent, /^Fundamentals data: Fixture · Updated: 01 Oct 2026$/, "the update date reads 01 Oct 2026");
  eq([t.F("fday")("2026-10-28"), t.F("fday")("2026-10-28T07:00:00+05:30"), t.F("fday")("later"), t.F("fday")(null)], ["28 Oct 2026", "28 Oct 2026", "later", "\u2014"], "dates: DD Mon YYYY; anything else as given; missing a dash");

  // ---- 6. the other two screen tabs follow the same rules (static: the tabs script is tested with its own harness in test_ui.js)
  const tabs = html.split("/* StockLens Phase 1 - Financial Health and Cash Flow & Growth tabs")[1].split("</script>")[0];
  re(tabs, /nz\("pe","P\/E",r2z\),nz\("pb","P\/B",r2z\),nz\("ev_ebitda","EV\/EBITDA",r2z\)/, "Returns and valuation: P/E, P/B and EV/EBITDA of 0 are dashes there too");
  re(tabs, /C\("roe","ROE %",r=>r2\(r\.roe\)\+\(typeof froeBad==="function"&&froeBad\(r\)\?FROE_FLAG:""\)/, "and an ROE that is not comparable is flagged there too");
  re(tabs, /<th\$\{c\.n\?' class="n"':''\}>/, "and its numeric headers are right-aligned");
  re(html, /<p class="note">Real company data from Upstox\. A dash \(\u2014\) means the value is not available\.<\/p>/, "the source note is kept, with the dash it uses");
  re(html, /<details class="src-more" style="padding:0 14px 10px"><summary>About these figures<\/summary><p class="note" style="padding:4px 0 0">A dash is shown when Upstox did not provide a value, and also for a P\/E, P\/B or EV\/EBITDA reported as exactly 0/, "the explanation is in an About these figures disclosure");
  console.log("Homepage fix tests passed (" + checks + " checks)");
})().catch((e) => { console.error("HOMEPAGE FIX TEST FAILED: " + e.message); process.exit(1); });
