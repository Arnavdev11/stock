// Run: node tests/test_company_search.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Phase 5F company search on the dashboard: a box that matches what is typed against the company names and symbols in out/fundamentals.json and out/company_profiles.json
// (case-insensitive, trimmed, exact / partial symbol and name), lists at most 10 results in a fixed text-based order (never by any financial value), opens the existing #stock=SYMBOL route,
// says "No matching company found." (and goes nowhere) for an unknown company, works from the keyboard, and leaves the rest of the page exactly as it was.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
// Phase 5I: behaviour is tested on the page as shipped (html). The byte-identity pins below are tested on PINHTML = the page minus the Phase 5I layer (legacy_5i.js, proven exact against aa4ace1 by test_global_navigation.js), because Phase 5I deliberately changes the route readers and the search mount.
const PINHTML = require("./legacy_5i.js").legacy(html), PINBLOCKS = PINHTML.split("<script>").slice(1).map((b) => b.split("</script>")[0]), PINDETAIL = PINBLOCKS.find((b) => b.includes("Stock Detail view"));
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">', SNAPTAG = '<script type="module" id="stocklens-snapshot">', SRCHTAG = '<script type="module" id="stocklens-search">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const detailCode = blocks.find((b) => b.includes("Stock Detail view")), fhCode = modOf(html, FHTAG), cmpCode = modOf(html, CMPTAG), snapCode = modOf(html, SNAPTAG), srchCode = modOf(html, SRCHTAG);
let checks = 0;
const OLDSPOT = `'<p class="snap-t">Detailed orders, capex, capacity expansion and management guidance will be added through the News &amp; Announcements research layer.</p>'`, NEWSPOT = `'<div data-growth-for="'+esc(s)+'"><p class="snap-t">Unavailable</p></div>'`;
const unesc = (x) => String(x).replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
ok(srchCode.length > 1000 && detailCode, "the search module and the Stock Detail block are found");

// ---------- the stubbed dashboard ----------
function load({ files = {}, delay = 0, detail = false, noHeader = false } = {}) {
  const fetched = [], docL = {}, win = {}, reg = {}, styles = [], w = { children: [] };
  const mk = (id) => { const e = { id, value: "", innerHTML: "", textContent: "", attrs: {}, listeners: {}, parentNode: null,
    setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; }, removeAttribute(k) { delete e.attrs[k]; }, hasAttribute(k) { return k in e.attrs; },
    addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); }, contains(x) { return !!(x && x.inside); } }; return e; };
  const nav = { tagName: "NAV", parentNode: w }, header = { tagName: "HEADER", parentNode: w };
  w.children = [nav, header]; w.insertBefore = (el, ref) => { el.parentNode = w; const i = w.children.indexOf(ref); w.children.splice(i < 0 ? w.children.length : i, 0, el); };
  const box = { id: "detail", innerHTML: "" }, classes = new Set();
  global.MutationObserver = undefined;
  global.window = { location: (() => { let h = ""; return { get hash() { return h ? "#" + h : ""; }, set hash(v) { h = String(v).replace(/^#/, ""); } }; })(), addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } },
    head: { appendChild: (e) => { styles.push(e); if (e.id) reg[e.id] = e; } },
    getElementById: (i) => (i === "detail" ? box : reg[i] || null), addEventListener: (t, f) => { (docL[t] = docL[t] || []).push(f); },
    querySelector: (q) => (q === "header" && !noHeader ? header : null), createElement: (tag) => { const e = mk(""); e.tag = tag; return e; } };
  global.fetch = async (u) => { fetched.push(u); if (delay) await sleep(delay); const f = files[u.split("/").pop()]; return { ok: f !== undefined && f !== null, json: async () => JSON.parse(JSON.stringify(f)) }; };
  // the section's children exist once it is inserted: register them by id, the way the browser would
  const realInsert = w.insertBefore; w.insertBefore = (el, ref) => { realInsert(el, ref); if (el.id === "stockSearch") { reg.stockSearch = el; for (const id of ["companySearch", "companySearchList", "companySearchMsg"]) reg[id] = mk(id); } };
  if (detail) (0, eval)(detailCode);
  (0, eval)(srchCode);
  const input = () => reg.companySearch, list = () => reg.companySearchList, msg = () => reg.companySearchMsg;
  const visible = (e) => !!e && (e.innerHTML !== "" || e.textContent !== "") && !e.hasAttribute("hidden");
  const P = { w, header, nav, box, classes, fetched, styles, reg, win, docL, S: window.SLSearch, input, list, msg,
    section: () => reg.stockSearch || null,
    results: () => (visible(list()) ? [...list().innerHTML.matchAll(/<li role="option" id="companySearchOpt(\d+)" data-symbol="([^"]*)" aria-selected="(true|false)"><span class="ss-sym">(.*?)<\/span><span class="ss-name">(.*?)<\/span>(?:<span class="ss-sec">(.*?)<\/span>)?<\/li>/g)].map((m) => ({ i: +m[1], symbol: unesc(m[4]), attr: unesc(m[2]), name: unesc(m[5]), sector: m[6] === undefined ? null : unesc(m[6]), selected: m[3] === "true" })) : []),
    message: () => (visible(msg()) ? msg().textContent : ""), listVisible: () => visible(list()),
    type: async (t, ms = 15) => { input().value = t; (input().listeners.input || []).forEach((f) => f({})); await sleep(ms); },
    focus: async (ms = 15) => { (input().listeners.focus || []).forEach((f) => f({})); await sleep(ms); },
    key: async (k, ms = 15) => { let pd = false; (input().listeners.keydown || []).forEach((f) => f({ key: k, preventDefault() { pd = true; } })); await sleep(ms); return pd; },
    click: (sym) => (list().listeners.click || []).forEach((f) => f({ target: { closest: (s) => (s === "[data-symbol]" ? { getAttribute: () => sym } : null) } })),
    clickAway: () => (docL.click || []).forEach((f) => f({ target: { inside: false } })), clickInside: () => (docL.click || []).forEach((f) => f({ target: { inside: true } })),
    hash: () => window.location.hash.replace(/^#/, "") };
  return P;
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();

// ---------- fixtures: identity fields plus financial decoys (the order must never follow those) ----------
const S1 = (symbol, company_name, sector, extra = {}) => Object.assign({ symbol, company_name, sector, pe: 20, pb: 3, roe: 15, roce: 18, roa: 5, ev_ebitda: 10 }, extra);
const BASE = () => [
  S1("TCS", "TATA CONSULTANCY SERV LT", "IT - Software", { pe: 99 }), S1("TATAMOTORS", "TATA MOTORS LIMITED", "Automobile", { pe: 1 }), S1("TATASTEEL", "TATA STEEL LIMITED", "Metals", { pe: 50 }),
  S1("INFY", "INFOSYS LIMITED", "IT - Software", { pe: 5 }), S1("HDFCBANK", "HDFC BANK LTD", "Bank", { pe: 80 }), S1("ICICIBANK", "ICICI BANK LTD.", "Bank", { pe: 2 }), S1("SBIN", "STATE BANK OF INDIA", "Bank", { pe: 40 }),
  S1("LT", "LARSEN & TOUBRO LTD.", "Engineering"), S1("M&M", "MAHINDRA & MAHINDRA LTD", "Automobile"), S1("BAJAJ-AUTO", "BAJAJ AUTO LIMITED", "Automobile"),
  S1("NOSEC", "NO SECTOR CORP", null), S1("XSS", 'A <b>&"X"</b> LTD', "<i>S</i>")];
const FUND = (stocks = BASE()) => ({ as_of: "2026-10-01", source: "Upstox", stocks });
const PROF = () => ({ as_of: "2026-10-04", stocks: [
  { symbol: "TCS", company_name: "Tata Consultancy Services Limited", sector: null, description: "d" },
  { symbol: "PROFONLY", company_name: "Profile Only Industries Limited", sector: "Profile Sector", description: "d" },
  { symbol: "INFY", company_name: "Infosys Limited", sector: "Profile sector must not replace", description: "d" }] });
const FILES = (o = {}) => Object.assign({ "fundamentals.json": FUND(), "company_profiles.json": PROF() }, o);
const syms = (r) => r.map((x) => x.symbol);

// ---------- an independent statement of the matching rule (written differently on purpose) ----------
const N = (s) => String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
function refSearch(docs, query) {
  const q = N(query); if (!q) return [];
  const qc = q.split(" ").join(""), words = q.split(" "), seen = {}, rows = [];
  for (const doc of docs) for (const x of (doc && Array.isArray(doc.stocks) ? doc.stocks : [])) {
    if (!x || typeof x.symbol !== "string" || !/^(?=.*[A-Z])[A-Z0-9&._-]{1,20}$/.test(x.symbol)) continue;
    const r = seen[x.symbol] || (seen[x.symbol] = { symbol: x.symbol, names: [] }); if (!seen[x.symbol].listed) { rows.push(r); r.listed = true; }
    if (typeof x.company_name === "string" && x.company_name.trim()) { const n = N(x.company_name); if (!r.names.includes(n)) r.names.push(n); } }
  const out = [];
  for (const r of rows) {
    const s = N(r.symbol), sc = s.split(" ").join(""), names = r.names; let t = 0;
    if (s === q || sc === qc) t = 1;
    else if (names.some((n) => n === q)) t = 2;
    else if (s.startsWith(q)) t = 3;
    else if (names.some((n) => n.startsWith(q))) t = 4;
    else if (names.some((n) => n.split(" ").some((_, i, a) => a.slice(i).join(" ").startsWith(q)))) t = 5;
    else if (s.includes(q)) t = 6;
    else if (names.some((n) => n.includes(q))) t = 7;
    else if (words.length > 1 && words.every((x) => x.length > 1) && names.some((n) => words.every((x) => n.includes(x)))) t = 8;
    if (t) out.push([t, r.symbol]); }
  out.sort((a, b) => (a[0] !== b[0] ? a[0] - b[0] : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  return out.slice(0, 10).map((x) => x[1]);
}
const ADVICE = /\b(buy|sell|hold|strong|bullish|bearish|score|scores|rating|rated|rank|ranks|ranking|ranked|winner|loser|best|worst|top|target|signal|recommend\w*|outperform\w*|underperform\w*|undervalued|overvalued|cheap|expensive|attractive|upside|downside|gainers?|losers?)\b/i;

(async () => {
  // ================= 0. structure and protection =================
  eq(PINBLOCKS.length, 7, "still exactly seven classic script PINBLOCKS"); eq((PINHTML.match(/<script/g) || []).length, 13, "thirteen script elements (Phase 5H.4 adds the shareholding module); previously twelve in all"); eq(PINHTML.split(SRCHTAG).length, 2, "one search module, with its own tag");
  ok(html.indexOf(SNAPTAG) < html.indexOf(SRCHTAG), "the search module comes after the snapshot module");
  let base = ""; try { base = cp.execSync("git show 6edaf45:index.html", { cwd: __dirname + "/..", encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
  if (base) {
    const ob = base.split("<script>").slice(1).map((x) => x.split("</script>")[0]);
    eq(PINBLOCKS.map(sha), ob.map(sha), "the seven classic PINBLOCKS are byte-identical to 6edaf45 (Phase 5E): dashboard, Stock Detail, charts, technicals, research, checklist"); eq(sha(modOf(PINHTML, FHTAG)), sha(modOf(base, FHTAG)), "the Financial History module is byte-identical");
    eq(modOf(PINHTML, CMPTAG), modOf(base, CMPTAG), "the comparison module is byte-identical"); eq(modOf(PINHTML, SNAPTAG), modOf(base, SNAPTAG).replace(OLDSPOT, NEWSPOT), "the Investor Snapshot module is byte-identical to 6edaf45 except the Future Growth Evidence placeholder (Phase 5G.2), now an empty spot for the growth module");
    eq(PINHTML.replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-shareholding">[\s\S]*?<\/script>\n/, ""), base.replace(OLDSPOT, NEWSPOT), "the page without the search and growth modules is identical to 6edaf45 (apart from the Future Growth Evidence placeholder): the markup, the tables, the styles, everything");
  }
  for (const id of ['id="rows"', 'id="tabs"', 'id="detail"', 'id="demo"', 'id="stat"']) ok(html.includes(id), "the dashboard still has " + id);
  re(html, /\.detail-mode \.w>\*:not\(#detail\)\{display:none!important\}/, "the page's own rule hides every dashboard child (the search box too) while a stock is open");

  // ================= A. the box =================
  let t = load();
  ok(t.section(), "the search section is added to the dashboard"); eq(t.section().id, "stockSearch", "its id"); eq(t.section().getAttribute("role"), "search", "a search landmark");
  eq(t.w.children.map((c) => c.tagName || c.id), ["NAV", "stockSearch", "HEADER"], "placed under the navigation and above the page heading (prominent, inside the same container as the dashboard)");
  const h = t.section().innerHTML;
  re(h, /<label for="companySearch">Search stocks<\/label>/, "an accessible label tied to the input, reading Search stocks"); re(h, /<input id="companySearch" type="search"/, "a search input"); re(h, /placeholder="Search company or symbol\.\.\."/, "the placeholder");
  re(h, /role="combobox"/, "combobox role"); re(h, /aria-autocomplete="list"/, "autocomplete list"); re(h, /aria-expanded="false"/, "collapsed at first"); re(h, /aria-controls="companySearchList"/, "controls the list");
  re(h, /autocomplete="off"/, "no browser autofill popup over ours"); re(h, /<ul class="ss-list" id="companySearchList" role="listbox" aria-label="Matching companies" hidden><\/ul>/, "an empty, hidden listbox with a label");
  re(h, /<p class="ss-msg" id="companySearchMsg" role="status" aria-live="polite" hidden><\/p>/, "a polite status line for messages"); no(h, /<form/, "no form (nothing is submitted anywhere)");
  eq(t.fetched.length, 0, "nothing is fetched when the page opens"); eq(t.styles.filter((s) => s.id === "stockSearchStyle").length, 1, "one style element");
  { const x = load({ noHeader: true }); eq(x.section(), null, "without the page heading to anchor on, nothing is added (no crash)"); eq(x.fetched.length, 0, "and nothing is fetched"); }

  // ================= the data loads once, on first use =================
  t = load({ files: FILES() }); await t.focus(40); eq([...t.fetched].sort(), ["out/company_profiles.json", "out/fundamentals.json"], "first use fetches exactly the two existing identity files");
  await t.focus(); await t.type("tcs", 40); await t.type("infy", 40); await t.key("Escape"); await t.type("tata", 40); eq(t.fetched.length, 2, "typing and focusing again never fetches again");
  { const x = load({ files: FILES() }); await x.type("tc", 40); eq(x.fetched.length, 2, "typing before focusing also loads them, once"); eq(syms(x.results()), ["TCS"], "and then shows the match"); }

  // ================= A. search basics =================
  t = load({ files: FILES() }); await t.focus(30);
  await t.type(""); eq(t.listVisible(), false, "empty input: no list"); eq(t.message(), "", "and no message");
  for (const ws of ["   ", "\t", "   ", "---", " & ", "...", "()"]) { await t.type(ws); eq([t.listVisible(), t.message()], [false, ""], "whitespace / punctuation only (" + JSON.stringify(ws) + "): no list, no message"); }
  for (const q of ["TCS", "tcs", "Tcs", "tCs", "  tcs  ", "\tTCS\n"]) { await t.type(q); eq(syms(t.results()), ["TCS"], "'" + q.trim() + "' in any case, with spaces around it: TCS"); }
  await t.type("INFY"); eq(syms(t.results()), ["INFY"], "exact symbol INFY"); await t.type("infy"); eq(syms(t.results()), ["INFY"], "exact symbol infy");
  await t.type("Infosys"); eq(syms(t.results()), ["INFY"], "company name Infosys"); await t.type("INFOSYS LIMITED"); eq(syms(t.results()), ["INFY"], "exact company name"); await t.type("infosys limited"); eq(syms(t.results()), ["INFY"], "exact company name, lower case");
  await t.type("Tata Consultancy"); eq(syms(t.results()), ["TCS"], "partial company name, two words"); await t.type("tata   consultancy"); eq(syms(t.results()), ["TCS"], "extra spaces inside are ignored"); await t.type("consultancy"); eq(syms(t.results()), ["TCS"], "a word from the middle of the name");
  await t.type("Tata Consultancy Services"); eq(syms(t.results()), ["TCS"], "the company_profiles name also matches (Services is not in the fundamentals name)"); await t.type("tata consultancy services limited"); eq(syms(t.results()), ["TCS"], "the profile name, in full");
  await t.type("tcs"); eq(t.results()[0].name, "TATA CONSULTANCY SERV LT", "but the shown name is the fundamentals name");
  await t.type("Tata"); eq(syms(t.results()), ["TATAMOTORS", "TATASTEEL", "TCS"], "a partial name: every Tata company, text-ordered");
  await t.type("tat"); eq(syms(t.results()), ["TATAMOTORS", "TATASTEEL", "TCS", "SBIN"], "partial symbol, then name start, then a fragment inside a name (STATE BANK), never by financial values");
  await t.type("tcs "); eq(syms(t.results()), ["TCS"], "trailing space trimmed"); await t.type(" tc"); eq(syms(t.results()), ["TCS"], "partial symbol, leading space"); await t.type("hdfc"); eq(syms(t.results()), ["HDFCBANK"], "partial symbol");
  await t.type("larsen & toubro"); eq(syms(t.results()), ["LT"], "an ampersand in the name"); await t.type("larsen toubro"); eq(syms(t.results()), ["LT"], "punctuation is ignored"); await t.type("L&T"); eq(syms(t.results())[0], "LT", "L&T finds LT"); no(syms(t.results()).join(","), /TCS|M&M/, "and one-letter fragments do not match everything");
  await t.type("m&m"); eq(syms(t.results()), ["M&M"], "M&M"); await t.type("bajaj-auto"); eq(syms(t.results()), ["BAJAJ-AUTO"], "BAJAJ-AUTO"); await t.type("bajaj auto"); eq(syms(t.results())[0], "BAJAJ-AUTO", "BAJAJ AUTO");
  await t.type("profile only"); eq(syms(t.results()), ["PROFONLY"], "a company that is only in company_profiles.json is found too (the data decides)"); eq(t.results()[0].sector, "Profile Sector", "with the profile's sector as the only one on file");
  await t.type("xyz123"); eq(t.results(), [], "no results"); eq(t.message(), "No matching company found.", "the exact message");

  // ================= B. suggestions =================
  await t.type("tcs"); let r = t.results()[0]; eq([r.symbol, r.name, r.sector], ["TCS", "TATA CONSULTANCY SERV LT", "IT - Software"], "symbol, company name and sector are shown"); eq(r.attr, "TCS", "the option carries the symbol to open");
  await t.type("infy"); eq(t.results()[0].sector, "IT - Software", "the fundamentals sector wins over the profile's"); await t.type("nosec"); eq(t.results()[0].sector, null, "no sector: no sector element, and no 'null' or 'undefined' text"); no(t.list().innerHTML, /null|undefined/, "no null / undefined in the list");
  await t.type("a <b>"); eq(syms(t.results()), ["XSS"], "special characters in a name are matched as text"); no(t.list().innerHTML, /<b>|<i>S/, "and never inserted as markup"); re(t.list().innerHTML, /A &lt;b&gt;&amp;&quot;X&quot;&lt;\/b&gt; LTD/, "the escaped name is shown intact");
  { // at most 10, in a fixed order
    const many = Array.from({ length: 25 }, (_, i) => S1("ALP" + String(i).padStart(2, "0"), "ALPHA COMPANY " + i, "S" + (i % 3), { pe: 100 - i })).concat(BASE());
    const x = load({ files: FILES({ "fundamentals.json": FUND(many.slice().reverse()) }) }); await x.focus(30); await x.type("alp");
    eq(x.results().length, 10, "at most 10 suggestions out of 25 matches"); eq(syms(x.results()), Array.from({ length: 10 }, (_, i) => "ALP" + String(i).padStart(2, "0")), "the first 10 by symbol, whatever order the file lists them in");
    await x.type("alpha company"); eq(x.results().length, 10, "10 for a name query as well"); await x.type("a"); eq(x.results().length, 10, "10 for a one-letter query"); await x.type("alp09"); eq(syms(x.results()), ["ALP09"], "an exact symbol among many is found, and alone");
    await x.type("alpha company 7"); eq(syms(x.results())[0], "ALP07", "an exact company name is first"); }
  { // deterministic: the same query, the same order, and the file order does not matter
    const QS = ["t", "tata", "bank", "a", "i", "l", "ltd", "limited", "s", "in", "o", "b", "co"], firsts = {};
    const a = load({ files: FILES() }); await a.focus(30);
    for (const q of QS) { await a.type(q); firsts[q] = syms(a.results()); await a.type(q); eq(syms(a.results()), firsts[q], "'" + q + "': repeated search gives the same order"); }
    const b = load({ files: FILES({ "fundamentals.json": FUND(BASE().reverse()), "company_profiles.json": { stocks: PROF().stocks.reverse() } }) }); await b.focus(30);
    for (const q of QS) { await b.type(q); eq(syms(b.results()), firsts[q], "'" + q + "': the order does not depend on the order of the files"); } }
  { // no financial ranking: reverse every financial value, the order does not move
    const flip = (s) => s.map((x, i) => Object.assign({}, x, { pe: 1000 - i * 7, pb: i, roe: 100 - i, roce: i * 3, roa: -i, ev_ebitda: i % 5, price: i * 11, change_pct: -i, volume: i })); const QS = ["t", "bank", "a", "i", "ltd", "limited", "in", "o", "tata", "s"], base = {}; const a = load({ files: FILES() }); await a.focus(30);
    for (const q of QS) { await a.type(q); base[q] = syms(a.results()); }
    await a.type("bank"); eq(syms(a.results()), ["HDFCBANK", "ICICIBANK", "SBIN"], "'bank': alphabetical by symbol (pe was 80, 2, 40), not by any value");
    const b = load({ files: FILES({ "fundamentals.json": FUND(flip(BASE())) }) }); await b.focus(30);
    for (const q of QS) { await b.type(q); eq(syms(b.results()), base[q], "'" + q + "': different pe / roe / price values give the same order"); } }
  { // a symbol is above a name, an exact name above a prefix, a prefix above a contains: the text rule, in tiers
    const D = FUND([S1("AB", "ZED CORP", "x"), S1("ABC", "AB INDUSTRIES", "x"), S1("ZZZ", "AB", "x"), S1("QQ", "XX AB YY", "x"), S1("AB2", "NOTHING", "x"), S1("CAB", "OTHER", "x"), S1("MM", "TEAB", "x")]);
    const x = load({ files: FILES({ "fundamentals.json": D, "company_profiles.json": null }) }); await x.focus(30); await x.type("ab");
    eq(syms(x.results()), ["AB", "ZZZ", "AB2", "ABC", "QQ", "CAB", "MM"], "exact symbol, exact name, symbol prefix (AB2 and ABC share a tier, so by symbol), word start, symbol contains, name contains");
    eq(syms(x.results()).slice(0, 2), ["AB", "ZZZ"], "an exact symbol first, then an exact name"); }
  // against the independent rule, for many queries
  { const docs = [FUND(), PROF()]; const x = load({ files: FILES() }); await x.focus(30); const queries = new Set(["a", "b", "c", "t", "ta", "tat", "in", "ind", "ltd", "limited", "bank", "state", "co", "m", "mm", "m m", "l t", "lt", "bajaj", "auto", "consult", "tata c", "x", "zz", "no", "sector", "corp", "profile", "only", "services", "infosys", "hdfc", "icici", "sbi", "of", "india", "the", "tata motors limited", "tata steel"]);
    for (const d of docs) for (const s of d.stocks) { const n = [s.symbol, s.company_name]; for (const v of n) { if (!v) continue; for (let i = 1; i <= v.length; i += 2) { queries.add(v.slice(0, i)); queries.add(v.slice(i - 1, i + 2).toLowerCase()); } } }
    let n = 0; for (const q of queries) { const want = refSearch(docs, q); eq(syms(x.S.search(x.S.entries(docs[0], docs[1]), q).map((y) => ({ symbol: y.e.symbol }))), want, "'" + q + "': same results and order as the independent statement of the rule"); n++; }
    ok(n > 150, "a broad set of queries was compared (" + n + ")"); }
  { // data hygiene
    const dirty = FUND([S1("tcs", "lower case symbol", "x"), S1("", "empty", "x"), S1(null, "null symbol", "x"), S1("OK1", "FIRST NAME", "Sec"), S1("OK1", "SECOND NAME", "Other"), null, 7, "x", { company_name: "no symbol" }, S1("A".repeat(21), "too long", "x"), S1("123", "digits only", "x"), S1("GOOD", "", "x"), S1("NONAME", null, "x")]);
    const x = load({ files: FILES({ "fundamentals.json": dirty, "company_profiles.json": null }) }); await x.focus(30);
    await x.type("lower"); eq(x.results(), [], "a lower-case symbol is not a stock symbol: ignored"); await x.type("empty"); eq(x.results(), [], "no empty symbol"); await x.type("digits"); eq(x.results(), [], "digits-only is not a symbol"); await x.type("too long"); eq(x.results(), [], "over-long symbol ignored"); await x.type("no symbol"); eq(x.results(), [], "no symbol, no entry");
    await x.type("ok1"); eq(x.results().length, 1, "a duplicated symbol is one result"); eq([x.results()[0].name, x.results()[0].sector], ["FIRST NAME", "Sec"], "the first record's name and sector are kept"); await x.type("second"); eq(syms(x.results()), ["OK1"], "the other name still finds it (it is only searchable, never shown)");
    await x.type("good"); eq([syms(x.results()), x.results()[0].name], [["GOOD"], ""], "a symbol with no company name is still listed, by symbol alone"); await x.type("noname"); eq(syms(x.results()), ["NONAME"], "likewise"); }
  { // only one file, or neither
    const a = load({ files: { "fundamentals.json": FUND() } }); await a.focus(30); await a.type("tata"); eq(syms(a.results()), ["TATAMOTORS", "TATASTEEL", "TCS"], "company_profiles.json missing: fundamentals alone is enough");
    const b = load({ files: { "company_profiles.json": PROF() } }); await b.focus(30); await b.type("profile"); eq(syms(b.results()), ["PROFONLY"], "fundamentals.json missing: the profiles alone are enough");
    const c = load({ files: {} }); await c.focus(30); await c.type("tcs"); eq(c.results(), [], "no data: no results"); eq(c.message(), "No matching company found.", "an empty company list is just 'no match' (nothing is invented)");
    const d = load({ files: { "fundamentals.json": { stocks: "bad" }, "company_profiles.json": { stocks: {} } } }); await d.focus(30); await d.type("tcs"); eq(d.message(), "No matching company found.", "malformed files: no crash, no match"); }
  { const x = load({ files: FILES(), delay: 40 }); await x.type("tcs", 5); eq(x.message(), "Loading the company list…", "while the files load, a loading line"); eq(x.listVisible(), false, "and no list yet"); await sleep(120); eq(syms(x.results()), ["TCS"], "when they arrive the results appear for what was typed"); }
  { const x = load({ files: FILES({ "fundamentals.json": FUND(Array.from({ length: 3 }, (_, i) => S1("Q" + i, "QUEUE " + i, "s"))) }), delay: 30 }); await x.type("q", 5); await x.type("queue 1", 5); await sleep(120); eq(syms(x.results()), ["Q1"], "the answer for the latest text is shown, not for an earlier keystroke"); }

  // ================= C. navigation =================
  t = load({ files: FILES() }); await t.focus(30);
  await t.type("tata"); t.click("TCS"); eq(t.hash(), "stock=TCS", "clicking a result opens #stock=TCS"); eq([t.listVisible(), t.input().value], [false, ""], "the list closes and the box is cleared"); eq(t.input().getAttribute("aria-expanded"), "false", "collapsed");
  await t.type("bank"); t.click("SBIN"); eq(t.hash(), "stock=SBIN", "the clicked symbol, not the first result");
  await t.type("m&m"); t.click("M&M"); eq(t.hash(), "stock=M%26M", "a symbol with & is encoded for the route"); await t.type("bajaj"); t.click("BAJAJ-AUTO"); eq(t.hash(), "stock=BAJAJ-AUTO", "a symbol with - is kept");
  window.location.hash = ""; t.click("<b>"); eq(t.hash(), "", "an invalid symbol in the markup opens nothing"); t.click(""); t.click(null); t.click("tcs"); eq(t.hash(), "", "empty, null or lower-case symbols open nothing");
  (t.list().listeners.click || []).forEach((f) => f({ target: { closest: () => null } })); (t.list().listeners.click || []).forEach((f) => f({ target: null })); eq(t.hash(), "", "a click that is not on a result does nothing");
  // Enter
  await t.type("tcs"); await t.key("Enter"); eq(t.hash(), "stock=TCS", "Enter on an exact symbol opens it"); window.location.hash = "";
  await t.type("INFOSYS LIMITED"); await t.key("Enter"); eq(t.hash(), "stock=INFY", "Enter on an exact company name opens it"); window.location.hash = "";
  await t.type("Tata Consultancy Services Limited"); await t.key("Enter"); eq(t.hash(), "stock=TCS", "Enter on an exact name from the profile opens it"); window.location.hash = "";
  await t.type("Infosys"); await t.key("Enter"); eq(t.hash(), "stock=INFY", "Enter when there is only one result opens it"); window.location.hash = "";
  await t.type("  tcs  "); await t.key("Enter"); eq(t.hash(), "stock=TCS", "Enter with spaces around the text"); window.location.hash = "";
  await t.type("tata"); let pd = await t.key("Enter"); eq(t.hash(), "", "Enter with several results and nothing highlighted goes nowhere (not a clear match)"); eq(syms(t.results()), ["TATAMOTORS", "TATASTEEL", "TCS"], "the list stays"); ok(pd, "Enter is consumed so nothing else reacts");
  await t.type("bank"); await t.key("Enter"); eq(t.hash(), "", "'bank' matches three companies: no guess");
  await t.type("xyz123"); await t.key("Enter"); eq(t.hash(), "", "an unknown company: Enter goes nowhere"); eq(t.message(), "No matching company found.", "and the message is shown"); eq([t.classes.has("detail-mode")], [false], "no detail mode");
  await t.type(""); await t.key("Enter"); eq(t.hash(), "", "Enter on an empty box goes nowhere"); await t.type("   "); await t.key("Enter"); eq(t.hash(), "", "Enter on spaces goes nowhere");
  await t.type("tcs"); await t.key("Enter"); eq(t.hash(), "stock=TCS", "an exact symbol that also appears inside other names still opens that company"); window.location.hash = "";
  { const x = load({ files: FILES({ "fundamentals.json": FUND([S1("AB", "ZED", "x"), S1("ZZ", "AB", "x")]), "company_profiles.json": null }) }); await x.focus(30); await x.type("ab"); await x.key("Enter"); eq(x.hash(), "stock=AB", "an exact symbol beats an exact name of another company"); window.location.hash = "";
    const y = load({ files: FILES({ "fundamentals.json": FUND([S1("P1", "TWIN CORP", "x"), S1("P2", "TWIN CORP", "x")]), "company_profiles.json": null }) }); await y.focus(30); await y.type("twin corp"); await y.key("Enter"); eq(y.hash(), "", "two companies with the same exact name: no guess"); }
  { const x = load({ files: FILES(), delay: 40 }); await x.type("tcs", 5); await x.key("Enter", 5); await sleep(60); eq(x.hash(), "", "Enter while the list is still loading waits (nothing opens by guess)"); await x.key("Enter"); eq(x.hash(), "stock=TCS", "and works once the data is there"); }
  // arrows, Escape, outside click
  t = load({ files: FILES() }); await t.focus(30); await t.type("bank");
  eq(t.results().map((x) => x.selected), [false, false, false], "nothing is highlighted at first"); await t.key("ArrowDown"); eq(t.results().map((x) => x.selected), [true, false, false], "ArrowDown highlights the first"); eq(t.input().getAttribute("aria-activedescendant"), "companySearchOpt0", "and announces it");
  await t.key("ArrowDown"); await t.key("ArrowDown"); eq(t.results().map((x) => x.selected), [false, false, true], "ArrowDown moves down"); await t.key("ArrowDown"); eq(t.results().map((x) => x.selected), [true, false, false], "and wraps to the top");
  await t.key("ArrowUp"); eq(t.results().map((x) => x.selected), [false, false, true], "ArrowUp wraps to the bottom"); await t.key("ArrowUp"); eq(t.results().map((x) => x.selected), [false, true, false], "ArrowUp moves up"); eq(t.input().getAttribute("aria-activedescendant"), "companySearchOpt1", "announced");
  await t.key("Enter"); eq(t.hash(), "stock=ICICIBANK", "Enter opens the highlighted result"); window.location.hash = "";
  await t.type("bank"); await t.key("ArrowUp"); eq(t.results().map((x) => x.selected), [false, false, true], "ArrowUp from nothing highlights the last"); await t.key("Enter"); eq(t.hash(), "stock=SBIN", "and Enter opens it"); window.location.hash = "";
  await t.type("bank"); eq(await t.key("Escape"), true, "Escape is consumed while the list is open"); eq([t.listVisible(), t.input().getAttribute("aria-expanded")], [false, "false"], "Escape closes the list"); eq(t.hash(), "", "and goes nowhere");
  await t.type("xyz123"); eq(t.message() !== "", true, "a message is showing"); await t.key("Escape"); eq(t.message(), "", "Escape closes the message too");
  await t.key("Escape"); eq(await t.key("Escape"), false, "Escape with nothing open is left to the browser");
  eq(await t.key("ArrowDown"), false, "arrows with no list are left alone"); eq(await t.key("a"), false, "other keys are left alone");
  await t.type("tata"); t.clickAway(); eq(t.listVisible(), false, "a click elsewhere on the page closes the list"); await t.type("tata"); t.clickInside(); eq(t.listVisible(), true, "a click inside the box keeps it open");
  await t.type("tata"); (t.list().listeners.mousedown || []).forEach((f) => { let pd2 = false; f({ preventDefault() { pd2 = true; } }); ok(pd2, "pressing on the list does not move the focus away from the box (so the click lands)"); });
  // the existing route, with the real Stock Detail block
  { const x = load({ files: FILES(), detail: true }); await x.focus(30); await x.type("tata"); x.click("TCS"); eq(x.hash(), "stock=TCS", "the search sets the hash"); ok(x.win.hashchange, "the Stock Detail block listens for hashchange"); x.win.hashchange(); await sleep(60);
    ok(x.classes.has("detail-mode"), "Stock Detail opened"); re(x.box.innerHTML, /Stock Detail: TCS/, "for TCS"); re(x.box.innerHTML, /id="detailBack"/, "with its Back to StockLens control");
    // Back to StockLens: the page's own handler clears the hash
    for (const f of x.docL.click) f({ target: { closest: (s) => (s === "#detailBack" ? {} : null) } }); eq(x.hash(), "", "Back to StockLens clears the hash (the page's own handler, unchanged)"); x.win.hashchange(); await sleep(20); eq(x.classes.has("detail-mode"), false, "and shows the dashboard again");
    await x.type("m&m"); x.click("M&M"); x.win.hashchange(); await sleep(60); re(x.box.innerHTML, /Stock Detail: M&amp;M/, "a symbol with & opens its Stock Detail"); window.location.hash = "#stock=INFY"; x.win.hashchange(); await sleep(60); re(x.box.innerHTML, /Stock Detail: INFY/, "a direct #stock=INFY still works");
    window.location.hash = "#stock=TCS"; x.win.hashchange(); await sleep(60); re(x.box.innerHTML, /Stock Detail: TCS/, "a direct #stock=TCS still works"); }

  // --- extra guards found by mutation testing ---
  { const x = load({ files: FILES() }); await x.focus(30);
    await x.type("consultancy tata"); eq(syms(x.results()), ["TCS"], "every word of the name, in any order, finds the company"); await x.type("toubro larsen"); eq(syms(x.results()), ["LT"], "any order, again"); await x.type("services consultancy"); eq(syms(x.results()), ["TCS"], "any order, using the profile name");
    await x.type("bank"); eq(await x.key("ArrowDown"), true, "ArrowDown is consumed while the list is open (the page does not scroll)"); eq(await x.key("ArrowUp"), true, "ArrowUp too");
    await x.type("tata"); await x.type("xyz123"); eq(x.results(), [], "an unknown company clears the old list"); eq(await x.key("ArrowDown"), false, "so arrows do nothing on the stale list"); await x.key("Enter"); eq(x.hash(), "", "and Enter goes nowhere");
    await x.type("bank"); await x.key("Escape"); eq(await x.key("ArrowDown"), false, "after Escape the arrows no longer act on the closed list"); await x.key("Enter"); eq(x.hash(), "", "Enter on the closed (not cleared) box and an ambiguous query still goes nowhere");
    await x.type("tcs"); ok(/<span class="ss-sym">TCS<\/span>/.test(x.list().innerHTML), "the symbol is shown"); await x.type("m&m"); ok(x.list().innerHTML.includes('<span class="ss-sym">M&amp;M</span>') && x.list().innerHTML.includes('data-symbol="M&amp;M"'), "a symbol with & is escaped in the markup, in the text and in the attribute"); }
  { const x = load({ files: FILES({ "fundamentals.json": FUND([S1("AB-C", "ONE CORP", "x"), S1("ABC", "TWO CORP", "x")]), "company_profiles.json": null }) }); await x.focus(30); await x.type("abc"); eq(syms(x.results()), ["AB-C", "ABC"], "two companies match the typed symbol equally"); await x.key("Enter"); eq(x.hash(), "", "Enter does not guess between two equally exact symbols"); }
  { const x = load({ files: FILES() }); const kids = x.w.children.length, sty = x.styles.length; (0, eval)(srchCode); eq([x.w.children.length, x.styles.length], [kids, sty], "running the module a second time adds neither a second box nor a second style"); }

  // ================= D. data safety =================
  const code = srchCode.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  eq((code.match(/fetch\(/g) || []).length, 1, "one fetch call"); eq([...new Set(code.match(/out\/[a-z_]+\.json/g))].sort(), ["out/company_profiles.json", "out/fundamentals.json"], "only the two existing identity files; no new JSON source");
  no(code, /scans|historical|financials\.json|financial_history|bigdeals|fiidii|news/, "no other data file");
  no(code, /https?:|\/\/[a-z]|token|apikey|api[_-]?key|secret|authorization|bearer|password|credential|xmlhttprequest|websocket|navigator\.sendBeacon|\.send\(/i, "no address, token, key, secret or other request mechanism");
  no(code, /localStorage|sessionStorage|indexedDB|document\.cookie|eval\(|document\.write|new Function|innerHTML\s*\+?=\s*[a-z]+\.value|\.sort\(function\(\)\{return Math/, "no storage, cookies or unsafe writes; nothing random");
  no(code, /Math\.random|Date\.now|new Date|setInterval|setTimeout/, "no randomness, clock or timers");
  for (const s of ["TCS", "INFY", "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "MARUTI", "TATA", "INFOSYS", "LARSEN", "BAJAJ", "HDFC", "ICICI", "AIRTEL", "SUZUKI"]) no(code, new RegExp(s), "no hard-coded stock universe: '" + s + "' is not in the code");
  no(code, /\[\s*"[A-Z0-9&._-]{2,20}"\s*,\s*"[A-Z0-9&._-]{2,20}"/, "no literal list of symbols");
  no(code, /[.\["'](pe|pb|roe|roce|roa|ev_ebitda|price|close|change_pct|change|volume|market_?cap|gain|loss|score|rank)\b/i, "the matching code never reads a financial field");
  re(code, /a\.e\.symbol<b\.e\.symbol/, "the final tie-break is the symbol text");
  { const x = load({ files: FILES() }); await x.focus(30); eq(Object.keys(x.S).sort(), ["MAX", "clear", "entries", "init", "norm", "search", "tier"], "the module's whole public surface"); eq(x.S.MAX, 10, "the limit is 10"); }
  { // the same company list for whatever the files hold: nothing hard-coded
    const mine = FUND([S1("QWERTY", "QWERTY INDUSTRIES", "Made Up")]); const x = load({ files: { "fundamentals.json": mine, "company_profiles.json": null } }); await x.focus(30); await x.type("tcs"); eq(x.results(), [], "TCS is not offered when it is not in the data"); await x.type("qwerty"); eq(syms(x.results()), ["QWERTY"], "a company that exists only in the data is offered"); }

  // ================= E. the rest of the page is exactly as it was =================
  no(code, /Investor Snapshot|data-snap|detailSnapshot|SLSnapshot|SLCompare|SLFinHistory|SLChart|SLTech|detailTech|detailChart/, "the search code does not touch the Investor Snapshot, comparison, Financial History or chart code");
  no(code, /document\.addEventListener\("(keydown|hashchange)|addEventListener\("hashchange"|\.location\.hash\s*=\s*""|replaceState|pushState/, "no second router: only the existing #stock= hash is set");
  eq((code.match(/location\.hash\s*=/g) || []).length, 1, "the hash is assigned in exactly one place"); re(code, /location\.hash="stock="\+encodeURIComponent\(sym\)/, "to the existing #stock=SYMBOL form, the same as Stock Detail's own go()");
  no(html.split(SRCHTAG)[0] + html.split(SRCHTAG)[1].split("</script>").slice(1).join("</script>"), /id="(stockSearch|companySearch)"/, "no search markup is in the static page: the box is created by the module (Phase 5I: into the global bar's empty host)");
  { const x = load({ files: FILES() }); await x.focus(30); await x.type("tata"); eq(x.box.innerHTML, "", "searching writes nothing into the Stock Detail area"); eq(x.classes.size, 0, "and does not change the page mode"); }

  // ================= F. wording =================
  { const x = load({ files: FILES() }); await x.focus(30); const seen = [x.section().innerHTML]; for (const q of ["tata", "bank", "xyz123", "tcs", "a", "   "]) { await x.type(q); seen.push(x.list().innerHTML, x.msg().textContent); } seen.push(x.msg().textContent);
    no(text(seen.join(" ")), ADVICE, "the box, the results and the messages contain no advice, ranking, score, signal, target or winner wording");
    no(text(seen.join(" ")), /\b(best stock|top stock|sell|buy|hold|bullish|bearish)\b/i, "no stock-picking words"); }
  no((code.match(/"[^"]*"|'[^']*'/g) || []).filter((m) => /[a-z]{4,}\s[a-z]{3,}/i.test(m)).join(" "), /\b(buy|sell|bullish|bearish|rating|score|winner|target|signal|best|top)\b/i, "no advisory words in the strings of the code");
  no(srchCode, /\b(gainers?|losers?|best stock|ranked by|sorted by)\b/i, "the comments never describe a performance order either");

  // ================= G. layout (the rules that keep it inside a 320px screen) =================
  const css = (srchCode.match(/var STYLE='([^]*?)';\nfunction addStyle/) || [])[1] || ""; ok(css.length > 300, "the style rules are found");
  re(css, /#stockSearch input\{[^}]*width:100%/, "the input fills its container and no more"); re(css, /#stockSearch input\{[^}]*box-sizing:border-box/, "padding and border are inside that width"); re(css, /#stockSearch input\{[^}]*min-width:0/, "it may shrink");
  re(css, /#stockSearch input\{[^}]*font:[^;}]*1rem/, "16px text, so a phone does not zoom the page on focus");
  re(css, /\.ss-box\{[^}]*min-width:0/, "the box may shrink"); re(css, /\.ss-list li\{[^}]*flex-wrap:wrap/, "a result wraps instead of widening"); re(css, /\.ss-name\{[^}]*overflow-wrap:anywhere/, "a long company name breaks instead of widening the page");
  re(css, /\.ss-sym\{[^}]*overflow-wrap:anywhere/, "so does a long symbol"); re(css, /\.ss-sec\{[^}]*overflow-wrap:anywhere/, "and a long sector"); re(css, /\.ss-msg\{[^}]*overflow-wrap:anywhere/, "and a message");
  no(css, /position:\s*(absolute|fixed)/, "the list is in the normal flow (an absolutely placed list could be clipped or sit outside the screen)"); no(css, /max-height|height:\s*\d+px/, "no fixed or maximum height, so nothing inside is cut off");
  no(css, /(^|[^-])(min-)?width:\s*\d+px|white-space:\s*nowrap|overflow-x|overflow:\s*(auto|scroll)/, "no fixed pixel width, no unwrapped text, no scrolling box"); no(css, /rgba?\(|#[0-9a-fA-F]{3,6}\b/, "colours come from the page's variables, so light and dark both work");
  for (const m of css.matchAll(/flex:\s*1 1 (\d+)em/g)) ok(+m[1] <= 14, "the name's basis is small enough to wrap on a phone: " + m[0]);
  re(css, /\.ss-list li:hover\{|\.ss-list li\[aria-selected=true\]/, "a highlighted result is visible"); re(css, /input:focus-visible\{[^}]*outline/, "keyboard focus is visible");
  { const x = load({ files: FILES() }); await x.focus(30); await x.type("a"); no(x.list().innerHTML, /style="[^"]*(width|min-width)\s*:\s*\d{2,}px/, "no fixed pixel sizes in the results"); eq((x.list().innerHTML.match(/<table/g) || []).length, 0, "no table in the results"); }

  // ================= H. the box is visible on the dashboard at every width (Phase 5F.1) =================
  // The page has no responsive rule that could hide it, the module does not look at the screen, and its own CSS keeps it inside 1280 / 390 / 320 px.
  { const m = /var STYLE='([\s\S]*?)';\nfunction addStyle/.exec(srchCode.replace(/'\n \+'/g, "")); ok(m, "the box's own style text is found"); const css = m[1], code = srchCode.replace(/\/\*[\s\S]*?\*\//g, "");
    const pageCss = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
    no(css, /@media|@container|@supports/, "the box's CSS has no media, container or supports rule: nothing in it depends on the screen");
    no(code, /innerWidth|innerHeight|outerWidth|clientWidth|screen\.|matchMedia|visualViewport|userAgent|navigator|orientation|ontouchstart|devicePixelRatio|resize/, "the module never looks at the screen, the device or the browser: it mounts the same way everywhere");
    no(code, /\bif\s*\([^)]*(width|height|mobile|touch)[^)]*\)/i, "and no branch of it depends on a width, a height or 'mobile'");
    for (const sel of ["#stockSearch", "#stockSearch .ss-box", "#stockSearch label", "#stockSearch input"]) { const rule = new RegExp("(^|\\}|')" + sel.replace(/[.#]/g, "\\$&") + "\\{([^}]*)\\}").exec(css.replace(/'\s*\+\s*'/g, "")); ok(rule, sel + " has a rule"); no(rule[2], /display:\s*none|visibility|opacity|height:\s*0|max-height|overflow:\s*hidden|clip|transform|position:\s*(absolute|fixed)|left:\s*-|top:\s*-|margin-left:\s*-|text-indent|font-size:\s*0/, sel + ": nothing in its rule can hide it, shrink it to nothing or move it off the screen"); }
    ok(!/display:\s*none/.test(css), "no display:none anywhere in the box's CSS (the list and message are hidden with the hidden attribute, never the box)");
    // the page's own CSS: nothing hides it except the two stock-open modes
    const hides = [...pageCss.matchAll(/([^{}]+)\{[^{}]*display:\s*none[^{}]*\}/g)].map((x) => x[1].trim());
    for (const sel of hides.filter((x) => !/^\.detail-mode /.test(x))) no(sel, /stockSearch|\.ss-|companySearch|\.w>\*|\.w\s*>\s*section|section(?![\w-])|\bsearch\b/i, "page rule '" + sel.slice(0, 60) + "' (display:none) cannot reach the search box");
    ok(/\.detail-mode \.w>\*:not\(#detail\)\{display:none!important\}/.test(pageCss), "only the Stock Detail rule hides the dashboard children, and it is tied to the detail-mode class");
    eq((html.match(/@media[^{]*\{/g) || []).filter((x) => !/prefers-color-scheme|max-width:640px/.test(x)).length, 0, "the page has no media rule other than dark mode and the comparison table's padding");
    for (const mq of html.match(/@media[^{]*\{[^}]*\}?[^}]*\}/g) || []) no(mq, /stockSearch|\.ss-|companySearch|\bsection\b|\.w\b|header|nav\b/, "no media rule touches the search, the page wrapper, the header or the nav");
    no(html.slice(html.indexOf("</style>")).replace(/<script[\s\S]*?<\/script>/g, ""), /stockSearch|companySearch|style="[^"]*display:\s*none/, "the static markup neither contains nor hides the box (it is added by the module)");
    // old mobile browsers: the module uses only syntax they run (a syntax error would drop the whole module, and with it the box)
    no(code, /\?\.|\?\?|\(\?<[=!]|\(\?<\w+>|\\p\{|\.replaceAll\(|\.at\(|Object\.hasOwn|structuredClone|import\(|\bawait\b|\bclass\s|\basync\b|#\w+\s*=|\bnew\s+Intl|\.flat\(|\.finally\(/, "no syntax or built-in that older phone browsers lack: no ?. ?? lookbehind, named groups, \\p{}, replaceAll, .at(), hasOwn, await, class fields");
    // arithmetic of the layout at the three widths: the page gives .w 18px each side (max 1040 wide); everything in the box is fluid
    const PAD = 18, widths = [[1280, Math.min(1040, 1280) - 2 * PAD], [390, 390 - 2 * PAD], [320, 320 - 2 * PAD]];
    const px = [...css.matchAll(/(?<![-\w])((?:min-|max-)?width|flex-basis)\s*:\s*(\d+(?:\.\d+)?)px/g)].map((x) => +x[2]); eq(px, [], "no fixed pixel width, min-width or basis in the box's CSS, so no width can be wider than a small screen");
    const mins = [...css.matchAll(/min\((\d+)px,100%\)/g)].map((x) => +x[1]); eq(mins, [], "(and no min() minimum either: the box has none)");
    for (const [vw, avail] of widths) { const box = css.match(/\.ss-box\{[^}]*padding:(\d+)px/), pad = +box[1]; ok(avail - 2 * pad - 2 >= 240, vw + "px: the input still has " + (avail - 2 * pad - 2) + "px inside the card, enough to type in"); }
    re(css, /#stockSearch input\{display:block;box-sizing:border-box;width:100%;min-width:0;/, "the input is a block, border-box, 100% wide and may shrink: it is exactly as wide as the card at every width");
    re(css, /\.ss-box\{[^}]*min-width:0/, "the card may shrink"); re(css, /#stockSearch\{margin:[^}]*\}/, "the section only has a margin");
    const fs = /#stockSearch input\{[^}]*font:600 (\d*\.?\d+)rem/.exec(css), padd = /#stockSearch input\{[^}]*padding:(\d+)px (\d+)px/.exec(css); ok(fs && +fs[1] >= 1, "the input's text is at least 16px (a phone browser does not zoom the page on focus)"); ok(padd && +padd[1] >= 12, "the input has 12px or more padding above and below: with a 16px line it is over 44px tall, an easy tap target"); }
  { // the box on the dashboard, with the page's real rule order: present, not hidden, whatever the screen
    for (const noHeader of [false]) { const x = load({ files: FILES() }); const sec = x.section(); ok(sec, "the box is mounted on the dashboard"); eq(Object.keys(sec.attrs).sort(), ["aria-label", "class", "id", "role"].filter((k) => k in sec.attrs).sort(), "its root carries only id / role / label / class attributes (no hidden, no style)"); ok(!("hidden" in sec.attrs) && !("style" in sec.attrs), "not hidden, no inline style");
      no(((/<label[^>]*>/.exec(sec.innerHTML) || [""])[0]) + ((/<input[^>]*>/.exec(sec.innerHTML) || [""])[0]) + ((/<div[^>]*>/.exec(sec.innerHTML) || [""])[0]), /\shidden|style=/, "no hidden or inline style on the card, the label or the input (only the list and the message start hidden)"); no(sec.innerHTML, /style="/, "no inline style anywhere in the box"); re(sec.innerHTML, /<input id="companySearch" type="search"[^>]*placeholder="Search company or symbol\.\.\."/, "the input with the same placeholder is there"); eq((sec.innerHTML.match(/<input/g) || []).length, 1, "exactly one input of any kind in the box (no second one for mobile)"); eq((sec.innerHTML.match(/id="companySearch"/g) || []).length, 1, "and it is the one search input");
      eq(x.w.children.filter((c) => c.id === "stockSearch").length, 1, "exactly one search section on the page"); eq(x.w.children.map((c) => c.id || c.tagName), ["NAV", "stockSearch", "HEADER"], "directly under the nav, above the page heading, in the page's own flow");
      for (const q of ["tata", "xyz123", "tcs"]) { await x.type(q); ok(!("hidden" in sec.attrs) && !("style" in sec.attrs), "typing '" + q + "' does not hide the box"); } await x.key("Escape"); await x.type("  "); ok(!("hidden" in sec.attrs), "closing the list or clearing the input does not hide the box"); }
    // the box exists on screens of any size: the stub has no screen at all, so mounting cannot depend on one
    for (const [w, h] of [[1280, 900], [390, 844], [320, 640], [280, 500], [0, 0]]) { const x = load({ files: FILES() }); Object.assign(global.window, { innerWidth: w, innerHeight: h, matchMedia: undefined }); ok(x.section() && !("hidden" in x.section().attrs), "mounted with a " + w + "x" + h + " window"); } }

  console.log("Company search tests passed (" + checks + " checks)");
})().catch((x) => { console.error("COMPANY SEARCH TEST FAILED:", x.message, x.stack ? "\n" + x.stack.split("\n").slice(1, 4).join("\n") : ""); process.exit(1); });
