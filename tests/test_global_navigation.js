// Run: node tests/test_global_navigation.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests Phase 5I: the global stock search and the persistent section navigation. Navigation only: the one existing Company Search is mounted in a
// global bar, section links reuse the existing sections of Stock Detail, and the address keeps the stock and the section (#stock=SYM&section=KEY).
const fs = require("fs"), cp = require("child_process"), assert = require("assert");
const { legacy } = require("./legacy_5i.js");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const modOf = (h, tag) => (h.split(tag)[1] || "").split("</script>")[0];
const NAVTAG = '<script type="module" id="stocklens-nav">', SRCHTAG = '<script type="module" id="stocklens-search">';
const navCode = modOf(html, NAVTAG), srchCode = modOf(html, SRCHTAG);
const blocks = html.split("<script>").slice(1).map((x) => x.split("</script>")[0]);
const detailCode = blocks.find((b) => b.includes("Stock Detail view")) || "";
const css = html.split("<style>")[1].split("</style>")[0];
ok(navCode.length > 2000 && srchCode.length > 2000 && detailCode.length > 2000, "the navigation, search and Stock Detail code are found");

// ---------- 1. the 5I layer is the ONLY change to the Phase 5H.6 page ----------
let base = ""; try { base = cp.execSync("git show aa4ace1:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
if (base) eq(legacy(html) === base, true, "the page minus the Phase 5I layer is byte-for-byte the Phase 5H.6 page: nothing else changed");
eq((html.match(/<script/g) || []).length, 14, "fourteen script elements: thirteen before, plus the navigation module");

// ---------- 2. the global bar: one search, persistent, outside the views that are hidden by detail/compare mode ----------
const bodyAt = html.indexOf("<body>"), wAt = html.indexOf('<div class="w">'), barAt = html.indexOf('<div id="globalBar">');
ok(barAt > bodyAt && barAt < wAt, "the global bar sits right after <body>, outside .w (so .detail-mode and .compare-mode, which hide .w's children, never hide it)");
re(html, /<div class="gb-search" id="globalSearchHost"><\/div>/, "the bar has the host for the global search");
{ const barHtml = html.slice(barAt, wAt), at = (t) => barHtml.indexOf(t);
  ok(at('id="gbBrand"') > -1 && at('id="gbBrand"') < at('id="gbSections"') && at('id="gbSections"') < at('id="globalSearchHost"'), "order of the bar: brand, then the section links, then the search at the far end (reading and tab order match the picture)");
  re(css, /#globalBar \.gb-search\{[^}]*margin-left:auto/, "the search is pushed to the extreme right of the bar");
  re(css, /#globalBar \.gb-sections ul\{[^}]*justify-content:flex-start/, "the section links sit together, next to the brand"); }
eq((html.match(/id="companySearch"/g) || []).length, 1, "the search input is written in exactly one place (the existing module): no second implementation");
eq((html.match(/SLSearch\s*=/g) || []).length, 1, "one search module");
no(navCode, /fundamentals\.json|company_profiles\.json|fetch\(|XMLHttpRequest|localStorage/, "the navigation reads no data, fetches nothing and stores nothing: it reuses the search module's own data");
no(navCode, /createElement\("input"\)|type="search"|placeholder=/, "the navigation module builds no search box of its own");
re(srchCode, /\$\("globalSearchHost"\)/, "the existing search module mounts itself in the global bar when it exists");
re(srchCode, /document\.querySelector\("header"\)/, "and still mounts under the header when there is no bar (unchanged fallback)");

// ---------- 3. the navigation module: routes and the registry of destinations ----------
function loadNav(hash = "", extra = {}) {
  const calls = { scroll: [] }, attrs = {}, listeners = {};
  global.MutationObserver = undefined;
  const mkEl = (id) => { const e = { id, innerHTML: "", textContent: "", attrs: {}, hidden: false, listeners: {}, setAttribute(k, v) { e.attrs[k] = String(v); }, removeAttribute(k) { delete e.attrs[k]; }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; }, hasAttribute(k) { return k in e.attrs; }, addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); }, contains() { return false; }, querySelectorAll() { return []; }, querySelector() { return null; }, getBoundingClientRect: () => ({ top: 0, height: 50 }) }; return e; };
  const els = { globalBar: mkEl("globalBar"), gbSections: mkEl("gbSections"), gbToggle: mkEl("gbToggle"), detail: mkEl("detail") };
  els.gbSections.attrs.hidden = ""; els.gbToggle.attrs.hidden = "";
  let h = hash;
  global.window = { location: { get hash() { return h; }, set hash(v) { h = String(v); } }, addEventListener: (t, f) => { listeners[t] = f; }, scrollTo: (x, y) => calls.scroll.push(y), pageYOffset: 0 };
  global.document = { getElementById: (i) => els[i] || null, addEventListener() {}, body: { classList: { add() {}, remove() {}, contains: () => false } } };
  Object.assign(els.detail, extra.detail || {});
  (0, eval)(navCode);
  return { N: window.SLNav, els, calls, listeners, setHash: (v) => { h = v; } };
}
const T = loadNav();
const N = T.N;
eq(N.SECTIONS.map((s) => s.key), ["overview", "fundamentals", "financials", "shareholding", "technical", "research"], "the destinations are exactly the sections that exist today, in menu order");
eq(N.SECTIONS.map((s) => s.label), ["Overview", "Fundamentals", "Financials", "Shareholding", "Technical", "Research"], "with readable labels");
ok(N.SECTIONS.every((s) => typeof s.find === "function"), "each destination finds an existing element of Stock Detail (no copied content)");
re(navCode, /#detailShareholding/, "Shareholding goes to the existing Shareholding Pattern section"); re(navCode, /#detailResearch/, "Research to the existing Stock Research section"); re(navCode, /#detailTech/, "Technical to the existing technical area");
re(navCode, /"Financial Health"/, "Financials to the existing Financial Health area"); re(navCode, /heading\(b,"Overview"\)/, "Fundamentals to the existing Fundamental Screen heading");
no(navCode.replace(/\/\*[\s\S]*?\*\//g, ""), /Quarterly|Results/i, "no Quarterly Results destination in the navigation code (comments aside)");
no(JSON.stringify(N.SECTIONS.map((s) => s.label + s.key)), /result|quarter/i, "no fake Quarterly Results link in the registry");
no(html.split(NAVTAG)[0].split('<div id="globalBar">')[1].split('<div class="w">')[0], /result|quarter/i, "nor in the bar's markup");
eq(N.hashFor("TCS", "shareholding"), "#stock=TCS&section=shareholding", "the address for TCS + Shareholding");
eq(N.hashFor("TCS", "financials"), "#stock=TCS&section=financials", "and TCS + Financials");
eq(N.hashFor("TCS"), "#stock=TCS", "a stock alone keeps the existing address");
eq(N.hashFor("M&M", "research"), "#stock=M%26M&section=research", "a symbol with & is encoded, so the section separator stays unambiguous");
eq(N.hashFor("TCS", "nonsense"), "#stock=TCS", "an unknown section is never written into an address");
eq(N.compareHash("TCS"), "#compare-pick=TCS", "Compare is the existing comparison route");
eq(N.parse("#stock=TCS&section=shareholding"), { symbol: "TCS", section: "shareholding", rawSection: "shareholding" }, "a direct link parses to stock and section");
eq(N.parse("#stock=TCS"), { symbol: "TCS", section: null, rawSection: null }, "the existing stock route still parses");
eq(N.parse("#stock=m%26m&section=technical"), { symbol: "M&M", section: "technical", rawSection: "technical" }, "the symbol is decoded and upper-cased, as the existing modules do");
for (const k of N.SECTIONS.map((s) => s.key)) eq(N.parse(N.hashFor("INFY", k)), { symbol: "INFY", section: k, rawSection: k }, "round trip " + k);
eq(N.parse("#stock=TCS&section=bogus"), { symbol: "TCS", section: null, rawSection: "bogus" }, "an unknown section fails safely: the stock still opens, the section is ignored");
for (const bad of ["", "#", "#stock=", "#stock=&section=research", "#stock=%E0%A4%A&section=research", "#stock=!!!", "#stock=TCS&section=", "#stock=TCS&section=a b", "#stock=TCS&section=research&x=1", "#stock=TCS&other=1", "#section=research", "#compare-pick=TCS", "#stock=TCS&section=" + "x".repeat(31), "#stock=" + "A".repeat(21), "#stock=123"]) eq(N.parse(bad), null, "unparseable or not a stock route: " + JSON.stringify(bad));
eq(N.parse(null), null, "no address at all");
{ // the existing routing in every module accepts the new form and rejects what it always rejected
  const routes = html.match(/var m=\/\^#stock=\(\[\^&\]\+\)\(\?:&section=\[A-Za-z0-9_-\]\{1,30\}\)\?\$\/\.exec\(window\.location\.hash\|\|""\);if\(!m\)return null;/g) || [];
  eq(routes.length, 8, "all eight route readers accept an optional &section=");
  const R = /^#stock=([^&]+)(?:&section=[A-Za-z0-9_-]{1,30})?$/;
  for (const [h, want] of [["#stock=TCS", "TCS"], ["#stock=TCS&section=shareholding", "TCS"], ["#stock=TCS&section=zzz", "TCS"], ["#stock=TCS&x=1", null], ["#stock=TCS&section=a/b", null], ["#compare-pick=TCS", null], ["", null]]) { const m = R.exec(h); eq(m ? m[1] : null, want, "reader: " + JSON.stringify(h)); }
  no(html, /\^#stock=\(\[\^&\]\+\)\$\//, "no reader still has the old strict pattern (they would drop the stock on a section link)");
}

// ---------- 4. the bar behaves: sections for the open stock, preserved symbol and section, active section, safe unknowns ----------
{ const t = loadNav("#stock=TCS&section=shareholding"); const nav = t.els.gbSections, bar = t.els.globalBar;
  ok(!nav.hasAttribute("hidden") && bar.hasAttribute("data-stock"), "on a stock the section navigation is shown");
  const links = [...nav.innerHTML.matchAll(/<a href="([^"]*)" data-gb-(?:section|compare)="([^"]*)">([^<]*)<\/a>/g)].map((m) => ({ href: m[1].replace(/&amp;/g, "&"), key: m[2], label: m[3] }));
  eq(links.map((l) => l.label), ["Overview", "Fundamentals", "Financials", "Shareholding", "Technical", "Research", "Compare"], "the top navigation lists the existing sections, then Compare (which exists)");
  ok(links.slice(0, 6).every((l) => l.href === "#stock=TCS&section=" + l.key), "every section link keeps the stock symbol and carries its section");
  eq(links[6].href, "#compare-pick=TCS", "Compare keeps the stock too");
  eq(nav.innerHTML.match(/aria-current="true"/g), null, "the links are plain; the active one is marked by the bar");
  eq(t.els.gbToggle.textContent, "Shareholding ▾", "the compact menu button names the current section (the active section is shown)");
  t.setHash("#stock=INFY&section=research"); t.listeners.hashchange();
  ok(/#stock=INFY&section=research/.test(nav.innerHTML.replace(/&amp;/g, "&")), "changing the stock rebuilds the links for the new stock"); eq(t.els.gbToggle.textContent, "Research ▾", "and the active section follows the address");
  t.setHash("#stock=INFY&section=bogus"); t.listeners.hashchange(); eq(t.els.gbToggle.textContent, "Sections ▾", "an unknown section: no section is marked, nothing breaks"); ok(!bar.hasAttribute("data-open"), "and the menu is closed");
  t.setHash(""); t.listeners.hashchange(); ok(nav.hasAttribute("hidden") && !bar.hasAttribute("data-stock"), "back on the dashboard the section navigation is hidden (the search stays)");
  t.setHash("#stock=zzz-nothing&section=shareholding"); t.listeners.hashchange(); ok(/#stock=ZZZ-NOTHING&section=shareholding/.test(nav.innerHTML.replace(/&amp;/g, "&")), "an unknown stock still routes (the Stock Detail view shows its own 'not found'); the bar does not crash");
}
{ // jumping to a section scrolls to the existing element, below the bar; a stock with the section absent is safe
  const el = (top) => ({ getBoundingClientRect: () => ({ top, height: 10 }), parentNode: null });
  const sh = el(1200); const detail = { querySelector: (q) => (q === "#detailShareholding" ? sh : null), querySelectorAll: () => [] };
  const t = loadNav("#stock=TCS&section=shareholding", { detail });
  eq(t.calls.scroll, [1200 - 50 - 8], "a direct link scrolls to the Shareholding section, below the 50px bar with 8px of air");
  const t2 = loadNav("#stock=TCS&section=technical", { detail }); eq(t2.calls.scroll, [], "a section that is not on the page (yet, or at all) scrolls nowhere and does not throw");
  const t3 = loadNav("#stock=TCS", { detail }); eq(t3.calls.scroll, [], "no section: no scroll");
}
{ // Back/Forward from a section to the bare stock returns to the top
  const detail = { querySelector: (q) => (q === "#detailShareholding" ? { getBoundingClientRect: () => ({ top: 900 }) } : null), querySelectorAll: () => [] };
  const t = loadNav("#stock=TCS&section=shareholding", { detail }); t.calls.scroll.length = 0; t.setHash("#stock=TCS"); t.listeners.hashchange(); eq(t.calls.scroll, [0], "going Back to the stock itself returns to its top");
}

// ---------- 5. the existing search, reused: finds TCS, opens TCS, mounts in the global bar ----------
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TATA CONSULTANCY SERV LT", sector: "IT - Software" }, { symbol: "RELIANCE", company_name: "RELIANCE INDUSTRIES LTD", sector: "Oil" }, { symbol: "HDFCBANK", company_name: "HDFC BANK LTD", sector: "Bank" }, { symbol: "M&M", company_name: "MAHINDRA & MAHINDRA LTD", sector: "Auto" }] };
async function loadSearch({ bar = true } = {}) {
  const fetched = [], docL = {}, reg = {}; let hash = "";
  const mk = (id) => { const e = { id, value: "", innerHTML: "", textContent: "", attrs: {}, listeners: {}, parentNode: null, children: [], setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; }, removeAttribute(k) { delete e.attrs[k]; }, hasAttribute(k) { return k in e.attrs; }, addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); }, contains() { return false; }, appendChild(c) { c.parentNode = e; e.children.push(c); if (c.id === "stockSearch") { reg.stockSearch = c; for (const id of ["companySearch", "companySearchList", "companySearchMsg"]) reg[id] = mk(id); } } }; return e; };
  const host = mk("globalSearchHost"); if (bar) reg.globalSearchHost = host;
  const w = { children: [], insertBefore(el) { el.parentNode = w; w.children.push(el); if (el.id === "stockSearch") { reg.stockSearch = el; for (const id of ["companySearch", "companySearchList", "companySearchMsg"]) reg[id] = mk(id); } } };
  const header = { tagName: "HEADER", parentNode: w };
  global.MutationObserver = undefined;
  global.window = { location: { get hash() { return hash; }, set hash(v) { hash = String(v); } }, addEventListener() {}, scrollTo() {} };
  global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, head: { appendChild: (e) => { if (e.id) reg[e.id] = e; } }, getElementById: (i) => reg[i] || null, addEventListener: (t, f) => { (docL[t] = docL[t] || []).push(f); }, querySelector: (q) => (q === "header" ? header : null), createElement: () => mk("") };
  global.fetch = async (u) => { fetched.push(u); const f = { "fundamentals.json": FUND, "company_profiles.json": { stocks: [] } }[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f)) }; };
  (0, eval)(srchCode);
  const P = { host, w, reg, fetched, S: window.SLSearch, hash: () => hash.replace(/^#/, ""),
    type: async (t) => { reg.companySearch.value = t; (reg.companySearch.listeners.input || []).forEach((f) => f({})); await sleep(25); },
    click: (sym) => (reg.companySearchList.listeners.click || []).forEach((f) => f({ target: { closest: (s) => (s === "[data-symbol]" ? { getAttribute: () => sym } : null) } })),
    key: (k) => (reg.companySearch.listeners.keydown || []).forEach((f) => f({ key: k, preventDefault() {} })),
    options: () => [...(reg.companySearchList.innerHTML.matchAll(/data-symbol="([^"]*)"/g))].map((m) => m[1]) };
  return P;
}
(async () => {
  { const P = await loadSearch();
    ok(P.host.children.some((c) => c.id === "stockSearch"), "the existing Company Search is mounted INSIDE the global bar");
    ok(!P.w.children.some((c) => c.id === "stockSearch"), "and nowhere else (no second copy under the header)");
    re(P.reg.stockSearch.innerHTML, /aria-label="Search stocks"/, "in the bar the input is labelled for screen readers"); no(P.reg.stockSearch.innerHTML, /<label/, "(no visible label taking room in the bar)");
    await P.type("TCS"); eq(P.options()[0], "TCS", "the global search finds TCS");
    P.click("TCS"); eq(P.hash(), "stock=TCS", "selecting TCS opens TCS (the existing Stock Detail route)");
    await P.type("reliance"); eq(P.options()[0], "RELIANCE", "company names work through the global search");
    await P.type("hdfc"); P.key("ArrowDown"); P.key("Enter"); eq(P.hash(), "stock=HDFCBANK", "Enter opens the top result, as before");
    await P.type("m&m"); P.click("M&M"); eq(P.hash(), "stock=M%26M", "a symbol with & opens correctly");
    await P.type("zzzzzz"); eq(P.options(), [], "an unknown stock lists nothing (fails safely)"); eq(P.hash(), "stock=M%26M", "and does not change the route");
    for (const f of P.fetched) re(f, /fundamentals\.json|company_profiles\.json/, "only the two existing data files are read: " + f);
  }
  { const P = await loadSearch({ bar: false }); ok(P.w.children.some((c) => c.id === "stockSearch"), "without a global bar the search still mounts under the header (unchanged fallback)"); }

  // ---------- 6. the markup and CSS: mobile navigation without overflow ----------
  re(html, /<button type="button" class="gb-toggle" id="gbToggle" aria-expanded="false" aria-controls="gbSections" hidden>/, "the mobile menu button exists, is a real button, and is wired to the section list");
  re(html, /<nav class="gb-sections" id="gbSections" aria-label="Stock sections" hidden><\/nav>/, "the section navigation is a labelled <nav>");
  re(css, /#globalBar\{[^}]*position:sticky;top:0/, "the bar stays on screen while scrolling (persistent)");
  re(css, /@container \(max-width:1000px\)\{#globalBar \.gb-toggle\{display:inline-flex/, "the compact navigation is a container query on the bar itself");
  re(css, /#globalBar\[data-open\] \.gb-sections\{display:block\}/, "the menu opens on demand");
  re(css, /#globalBar \.gb-sections a\{min-height:44px/, "touch targets are at least 44px on small screens");
  const bar = css.slice(css.indexOf("#globalBar{"), css.indexOf("@container (max-width:1000px)") + 900);
  no(bar, /overflow-x\s*:\s*(auto|scroll)|white-space\s*:\s*nowrap|(?<![-\w])width\s*:\s*\d+(px|rem)|min-width\s*:\s*\d+(px|rem)|transition|animation|@keyframes/, "no fixed widths, no nowrap, no scroll-strip hack, no animation in the bar");
  no(bar, /@media/, "no new media rules (the existing tests pin those); the bar adapts to its own width");
  re(bar, /gb-sections ul\{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap/, "desktop: the links wrap instead of overflowing");
  re(bar, /grid-template-columns:repeat\(auto-fit,minmax\(min\(130px,100%\),1fr\)\)/, "mobile: a grid whose columns can never be wider than the screen");
  re(bar, /overflow-wrap:anywhere/, "long labels wrap");
  eq(css.match(/@media[^{]+/g).length >= 1, true, "(the existing media rules are untouched)");
  console.log("Global navigation tests passed (" + checks + " checks)");
})().catch((e) => { console.error("GLOBAL NAVIGATION TEST FAILED:", e && e.message ? e.message : e); if (e && e.stack) console.error(e.stack.split("\n").slice(0, 4).join("\n")); process.exit(1); });
