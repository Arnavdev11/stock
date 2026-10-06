// Run: node tests/test_growth_events.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Phase 5G.2 Future Growth Evidence: a small curated file of events (orders, order book, capex, capacity, acquisitions, new businesses), each taken by hand from a primary official document and carrying
// its source name, exact source URL and the quote that supports it, shown inside the Investor Snapshot on Stock Detail only. The kinds are never mixed (order announced / inflow / TCV / order book; capex
// actual / approved / planned; capacity planned / operational; acquisition announced / completed), a value the source does not give is null (never zero), a category without an entry says "Unavailable" (never "no orders"),
// and nothing is advice. The page makes no request except the one static file.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), cp = require("child_process");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const FHTAG = '<script type="module">', CMPTAG = '<script type="module" id="stocklens-compare">', SNAPTAG = '<script type="module" id="stocklens-snapshot">', SRCHTAG = '<script type="module" id="stocklens-search">', GROWTAG = '<script type="module" id="stocklens-growth">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const detailCode = blocks.find((b) => b.includes("Stock Detail view")), techCode = blocks.find((b) => b.includes("Phase 3 Step 3 - Technical Snapshot")), researchCode = blocks.find((b) => b.includes("Phase 4 Step 1 - Stock Research")),
  fhCode = modOf(html, FHTAG), cmpCode = modOf(html, CMPTAG), snapCode = modOf(html, SNAPTAG), srchCode = modOf(html, SRCHTAG), grCode = modOf(html, GROWTAG);
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const un = (s) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();
const unesc = (s) => String(s).replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
ok(grCode.length > 1000 && snapCode && srchCode && detailCode && techCode && researchCode && fhCode && cmpCode, "all the page's scripts are found");

// ---------- the data, as shipped ----------
const RAW = fs.readFileSync(ROOT + "/data/growth_events.json", "utf8"), RAW_OUT = fs.readFileSync(ROOT + "/out/growth_events.json", "utf8");
const DOC = JSON.parse(RAW), EVENTS = DOC.events;
const TEST_STOCKS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI"];
const TYPES = ["order", "order_book", "capex", "capacity", "acquisition", "business"];
const MEASURES = ["order_announced", "order_inflow", "deal_value_tcv", "order_book", "capex_actual", "capex_approved", "capex_planned", "capacity_planned", "capacity_operational"];
const STATUSES = ["announced", "completed", "approved", "planned"];
const CATS = [["orders", "Orders / Contracts", "order"], ["order_book", "Order Book", "order_book"], ["capex", "Capex", "capex"], ["capacity", "Capacity Expansion", "capacity"], ["acquisitions", "Acquisitions / JVs", "acquisition"], ["business", "New Businesses", "business"]];
const MLABEL = { order_announced: "Order announced", order_inflow: "Order inflow", deal_value_tcv: "Deal value (TCV)", order_book: "Order book", capex_actual: "Capex (actual)", capex_approved: "Capex (approved)", capex_planned: "Capex (planned)", capacity_planned: "Capacity (planned)", capacity_operational: "Capacity (operational)" };
const SLABEL = { announced: "Announced", completed: "Completed", approved: "Approved", planned: "Planned" };
// independent statement of what each kind may carry
const REF_MEASURES = { order: ["order_announced", "order_inflow", "deal_value_tcv"], order_book: ["order_book"], capex: ["capex_actual", "capex_approved", "capex_planned"], capacity: ["capacity_planned", "capacity_operational"], acquisition: [], business: [] };
const REF_STATUS = { order_announced: ["announced"], order_inflow: [], deal_value_tcv: [], order_book: [], capex_actual: ["completed"], capex_approved: ["approved"], capex_planned: ["planned"], capacity_operational: ["completed"], capacity_planned: ["planned", "approved", "announced"] };
const ADVICE = /\b(buy|sell|hold|strong|weak|bullish|bearish|positive|negative|good|bad|attractive|score|scores|rating|rated|rank|ranks|ranking|ranked|winner|loser|best|worst|top|target|signal|recommend\w*|outperform\w*|underperform\w*|undervalued|overvalued|cheap|expensive|upside|downside|gainers?|losers?|likely to rise|future return)\b/i;
const AGGREGATOR = /(moneycontrol|economictimes|business-standard|livemint|tipranks|alphaspread|investing\.com|seekingalpha|finology|screener\.in|trendlyne|scanx|upstox|apify|yahoo|marketscreener|outlookbusiness|angelone|msn\.com|zerodha|groww|etmarkets|ndtv|hindustantimes|financialexpress|thehindu|reuters|bloomberg|stocktitan|prnewswire|quartr|simplehai|axisdirect|icicidirect|finbox|thestreet|substack|scribd|wikipedia)/i;
const OFFICIAL_HOSTS = ["www.ril.com", "www.tcs.com", "www.infosys.com", "nsearchives.nseindia.com", "www.bseindia.com", "www.marutisuzuki.com", "assets.airtel.in", "itcportal.com"];

// ---------- the stubbed page ----------
function load({ doc = DOC, hash = "#stock=TCS", spotFor = "TCS", delay = 0, failFetch = false, status = 200 } = {}) {
  const fetched = [], styles = [], obs = [];
  const mk = (id) => { const e = { id, attrs: {}, innerHTML: "", parentNode: null, setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; } }; return e; };
  let spot = null;
  const box = { id: "detail", querySelector: (sel) => (sel === "[data-growth-for]" ? spot : null) };
  global.MutationObserver = function (cb) { this.observe = (el, opts) => { if (el === box) obs.push({ cb, opts }); }; };
  global.window = { location: { hash } };
  global.document = { head: { appendChild: (e) => styles.push(e) }, getElementById: (i) => (i === "detail" ? box : i === "growthStyle" ? styles.find((x) => x.id === "growthStyle") || null : null), createElement: (t) => { const e = mk(""); e.tag = t; return e; } };
  global.fetch = async (u, o) => { fetched.push({ u, o }); if (delay) await sleep(delay); if (failFetch) throw new Error("offline"); return { ok: status === 200, json: async () => JSON.parse(JSON.stringify(doc)) }; };
  (0, eval)(grCode);
  const P = { fetched, styles, obs, G: window.SLGrowth,
    spot: () => spot, notify: () => obs.forEach((o) => o.cb()),
    place: (sym, { notify = true } = {}) => { if (spot) spot.parentNode = null; spot = mk(""); spot.attrs["data-growth-for"] = sym; spot.innerHTML = '<p class="snap-t">Unavailable</p>'; spot.parentNode = box; if (notify) P.notify(); return spot; },
    html: () => (spot ? spot.innerHTML : ""), state: () => (spot ? spot.getAttribute("data-growth-state") : null) };
  if (spotFor) P.place(spotFor, { notify: false });
  return P;
}
const cats = (h) => { const out = {}; for (const c of CATS) { const m = new RegExp('<div class="gr-c" data-gr="' + c[0] + '"><h5>([^<]*)</h5>([\\s\\S]*?)(?=<div class="gr-c" data-gr="|</div><p class="snap-n">)').exec(h); out[c[0]] = m ? { title: m[1], body: m[2].replace(/<\/div>$/, "") } : null; } return out; };
const events = (body) => body.split('<div class="gr-e">').slice(1).map((x) => x);
const attr = (s, name) => { const m = new RegExp(name + '="([^"]*)"').exec(s); return m ? unesc(m[1]) : null; };
const E = (o = {}) => Object.assign({ symbol: "TCS", date: "2026-07-09", event_type: "order", title: "A title", description: null, value: null, currency: null, unit: null, measure: "order_announced", segment: null, status: null, source_name: "Company release", source_url: "https://www.tcs.com/x.pdf", source_date: null, retrieved_on: "2026-10-05", evidence_quote: "A quote" }, o);

(async () => {
  // ================= 0. structure and protection =================
  eq(grCode.length > 0 && html.split(GROWTAG).length, 2, "one growth module, with the exact tag");
  eq(blocks.length, 7, "still exactly the seven classic script blocks"); eq((html.match(/<script/g) || []).length, 13, "thirteen script elements (Phase 5H.4 adds the shareholding module); previously twelve in all (Phase 5F: search, Phase 5G.2: growth)");
  ok(html.indexOf(GROWTAG) > html.indexOf(SRCHTAG) && html.indexOf(GROWTAG) > html.indexOf(SNAPTAG), "the growth module comes after the snapshot and search modules");
  let base = ""; try { base = cp.execSync("git show be3940b:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { base = ""; }
  if (base) {
    const ob = base.split("<script>").slice(1).map((x) => x.split("</script>")[0]);
    eq(blocks.map(sha), ob.map(sha), "the seven classic blocks are byte-identical to be3940b (Phase 5F): dashboard, Stock Detail, charts, technicals, research, checklist");
    eq(sha(fhCode), sha(modOf(base, FHTAG)), "the Financial History module is byte-identical"); eq(cmpCode, modOf(base, CMPTAG), "the comparison module is byte-identical"); eq(srchCode, modOf(base, SRCHTAG), "the search module is byte-identical");
    const OLD = `'<p class="snap-t">Detailed orders, capex, capacity expansion and management guidance will be added through the News &amp; Announcements research layer.</p>'`, NEW = `'<div data-growth-for="'+esc(s)+'"><p class="snap-t">Unavailable</p></div>'`;
    ok(modOf(base, SNAPTAG).includes(OLD), "the old placeholder is in the base"); eq(snapCode, modOf(base, SNAPTAG).replace(OLD, NEW), "the Investor Snapshot module differs from be3940b only by its Future Growth Evidence placeholder, which is now the empty spot for the growth module");
    eq(html.replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-shareholding">[\s\S]*?<\/script>\n/, ""), base.replace(OLD, NEW), "the page without the growth module is identical to be3940b apart from that one placeholder: markup, tables, styles, everything");
  }
  eq(RAW, RAW_OUT, "data/growth_events.json and out/growth_events.json are the same file, byte for byte");
  eq(fs.readdirSync(ROOT + "/out"), ["growth_events.json"], "out/ holds only the curated file (no generated data is shipped)"); eq(fs.readdirSync(ROOT + "/data"), ["growth_events.json"], "and so does data/");
  eq(Object.keys(DOC).sort(), ["as_of", "events", "note"], "the file has as_of, note and events");

  // ================= 1. data model =================
  const G = load().G;
  eq([G.TYPES, G.MEASURES, G.STATUSES], [TYPES, MEASURES, STATUSES], "the controlled values are exactly the specified ones");
  ok(G.valid(E()), "a complete event is valid");
  for (const t of TYPES) { const ms = REF_MEASURES[t].length ? REF_MEASURES[t] : [null]; for (const m of ms) ok(G.valid(E({ event_type: t, measure: m })), t + " with measure " + m + " is valid"); }
  for (const bad of ["deal", "Order", "ORDER", "orders", "", " order", null, undefined, 5, "guidance", "analyst_estimate", "order|capex"]) ok(!G.valid(E({ event_type: bad })), "invalid event_type " + JSON.stringify(bad) + " is rejected");
  for (const f of ["symbol", "date", "event_type", "title", "source_name", "source_url", "retrieved_on", "evidence_quote"]) { const e = E(); delete e[f]; ok(!G.valid(e), f + " is required (missing)"); for (const v of [null, "", "   ", 0, false]) ok(!G.valid(E({ [f]: v })), f + " is required (" + JSON.stringify(v) + ")"); }
  for (const f of ["description", "value", "currency", "unit", "measure", "segment", "status", "source_date"]) { const e = E({ event_type: "business", measure: null }); delete e[f]; ok(G.valid(e), f + " may be missing"); ok(G.valid(E({ event_type: "business", measure: null, [f]: null })), f + " may be null"); }
  for (const v of [0, -1, -0.5, NaN, Infinity, -Infinity, "5", "0", true, [], {}]) ok(!G.valid(E({ value: v, currency: "INR", unit: "crore" })), "value " + JSON.stringify(v) + " is rejected: zero or a non-number never stands for 'not disclosed'");
  for (const v of [1, 0.5, 9.5, 131107, 4356]) ok(G.valid(E({ value: v, currency: "INR", unit: "crore" })), "value " + v + " is valid");
  ok(!G.valid(E({ value: 5 })), "a value without currency and unit is rejected"); ok(!G.valid(E({ value: 5, currency: "INR" })), "a value without a unit is rejected"); ok(!G.valid(E({ value: 5, unit: "crore" })), "a value without a currency is rejected"); ok(G.valid(E({ value: null })), "no value is valid");
  for (const u of ["", "http://www.tcs.com/x.pdf", "ftp://x.com/a", "javascript:alert(1)", "data:text/html,x", "//www.tcs.com/x", "/x.pdf", "www.tcs.com/x.pdf", "https://", "https://a b.com/x", "https://user@host.com/x", "https://host.com/a b", " https://www.tcs.com/x", 5, null, {}, "HTTPS://www.tcs.com/x"]) ok(!G.valid(E({ source_url: u })), "source_url " + JSON.stringify(u) + " is rejected");
  for (const u of ["https://www.tcs.com/x.pdf", "https://nsearchives.nseindia.com/corporate/a.pdf", "https://x.com/a?b=1&c=2#d", "https://x.com:8443/a"]) ok(G.valid(E({ source_url: u })), "source_url " + u + " is accepted");
  for (const s of ["tcs", "T CS", "", "1234", "TC$", "A".repeat(21), 5]) ok(!G.valid(E({ symbol: s })), "symbol " + JSON.stringify(s) + " is rejected");
  for (const d of ["2026-02-30", "2026-13-01", "2026-7-9", "09-07-2026", "2026/07/09", "20260709", "", "July 9, 2026", "2026-07-09T00:00:00Z"]) { ok(!G.valid(E({ date: d })), "date " + d + " is rejected"); ok(!G.valid(E({ retrieved_on: d })), "retrieved_on " + d + " is rejected"); ok(!G.valid(E({ source_date: d })), "source_date " + d + " is rejected"); }
  ok(G.valid(E({ date: "2024-02-29" })), "a real leap day is a valid date"); ok(!G.valid(E({ date: "2025-02-29" })), "a false leap day is not"); ok(G.valid(E({ source_date: "2026-07-09" })), "a source_date is accepted");
  for (const o of [null, undefined, 5, "x", [], true]) ok(!G.valid(o), "a non-object event " + JSON.stringify(o) + " is rejected");
  // every kind x measure x status, against the independent statement
  for (const t of TYPES.concat(["bogus"])) for (const m of [null].concat(MEASURES, ["bogus"])) for (const s of [null].concat(STATUSES, ["bogus"])) {
    const exp = TYPES.includes(t) && (m === null ? REF_MEASURES[t].length === 0 : MEASURES.includes(m) && REF_MEASURES[t].includes(m)) && (s === null || STATUSES.includes(s)) && (m === null || s === null || REF_STATUS[m].includes(s));
    eq(G.valid(E({ event_type: t, measure: m, status: s })), exp, "kind " + t + " / measure " + m + " / status " + s + (exp ? " is accepted" : " is rejected")); }
  for (const t of ["order", "order_book", "capex", "capacity"]) ok(!G.valid(E({ event_type: t, measure: null })), t + " without a measure is not shown: the kinds cannot be told apart");
  for (const t of ["acquisition", "business"]) for (const m of MEASURES) ok(!G.valid(E({ event_type: t, measure: m })), t + " cannot carry the measure " + m);

  // ================= 2. the distinctions =================
  { const P = load({ spotFor: null }); const types = (a) => a.map((x) => x[0]);
    const mk = (symbol) => ({ events: [
      E({ symbol, event_type: "order", measure: "order_announced", title: "ANNOUNCED", status: "announced", value: 800, currency: "USD", unit: "million" }), E({ symbol, event_type: "order", measure: "order_inflow", title: "INFLOW", value: 4356, currency: "INR", unit: "billion" }),
      E({ symbol, event_type: "order", measure: "deal_value_tcv", title: "TCV", value: 9.5, currency: "USD", unit: "billion" }), E({ symbol, event_type: "order_book", measure: "order_book", title: "BOOK", value: 7403, currency: "INR", unit: "billion" }),
      E({ symbol, event_type: "capex", measure: "capex_actual", title: "CAPEX-ACTUAL", value: 100, currency: "INR", unit: "crore" }), E({ symbol, event_type: "capex", measure: "capex_approved", status: "approved", title: "CAPEX-APPROVED", value: 200, currency: "INR", unit: "crore" }),
      E({ symbol, event_type: "capex", measure: "capex_planned", status: "planned", title: "CAPEX-PLANNED", value: 300, currency: "INR", unit: "crore" }), E({ symbol, event_type: "capacity", measure: "capacity_planned", status: "planned", title: "CAP-PLANNED" }),
      E({ symbol, event_type: "capacity", measure: "capacity_operational", status: "completed", title: "CAP-OPERATIONAL" }), E({ symbol, event_type: "acquisition", measure: null, status: "announced", title: "ACQ-ANNOUNCED" }),
      E({ symbol, event_type: "acquisition", measure: null, status: "completed", title: "ACQ-COMPLETED", source_url: "https://www.tcs.com/y.pdf" }), E({ symbol, event_type: "business", measure: null, title: "BUSINESS", value: 5610, currency: "INR", unit: "million", status: "approved" })] });
    const c = cats(P.G.render("TCS", mk("TCS")));
    const where = (title) => Object.keys(c).filter((k) => c[k] && c[k].body.includes(">" + title + "<"));
    for (const [title, cat] of [["ANNOUNCED", "orders"], ["INFLOW", "orders"], ["TCV", "orders"], ["BOOK", "order_book"], ["CAPEX-ACTUAL", "capex"], ["CAPEX-APPROVED", "capex"], ["CAPEX-PLANNED", "capex"], ["CAP-PLANNED", "capacity"], ["CAP-OPERATIONAL", "capacity"], ["ACQ-ANNOUNCED", "acquisitions"], ["ACQ-COMPLETED", "acquisitions"], ["BUSINESS", "business"]]) eq(where(title), [cat], title + " is shown in " + cat + " only");
    const lab = (title) => { const body = c[where(title)[0]].body; const ev = events(body).find((x) => x.includes(">" + title + "<")); return { m: (/<span class="gr-l">([^<]*)<\/span>/.exec(ev) || [])[1] || null, s: (/<span class="gr-s">([^<]*)<\/span>/.exec(ev) || [])[1] || null, v: (/<b class="gr-v">([^<]*)<\/b>/.exec(ev) || [])[1] || null }; };
    eq(lab("ANNOUNCED"), { m: "Order announced", s: "Announced", v: "US$ 800 million" }, "an announced order is 'Order announced'"); eq(lab("INFLOW"), { m: "Order inflow", s: null, v: "₹ 4,356 billion" }, "order inflow is 'Order inflow', not an order book");
    eq(lab("TCV"), { m: "Deal value (TCV)", s: null, v: "US$ 9.5 billion" }, "TCV is 'Deal value (TCV)', not an order book"); eq(lab("BOOK"), { m: "Order book", s: null, v: "₹ 7,403 billion" }, "only a real order book is 'Order book'");
    eq(lab("CAPEX-ACTUAL").m, "Capex (actual)", "capex actual"); eq(lab("CAPEX-APPROVED"), { m: "Capex (approved)", s: "Approved", v: "₹ 200 crore" }, "capex approved is not 'actual'"); eq(lab("CAPEX-PLANNED"), { m: "Capex (planned)", s: "Planned", v: "₹ 300 crore" }, "capex planned is not 'actual'");
    eq(lab("CAP-PLANNED"), { m: "Capacity (planned)", s: "Planned", v: null }, "planned capacity is not 'operational'"); eq(lab("CAP-OPERATIONAL"), { m: "Capacity (operational)", s: "Completed", v: null }, "operational capacity is not 'planned'");
    eq(lab("ACQ-ANNOUNCED"), { m: null, s: "Announced", v: null }, "an announced acquisition says Announced, not Completed"); eq(lab("ACQ-COMPLETED"), { m: null, s: "Completed", v: null }, "Completed only where the data says so");
    eq(lab("BUSINESS"), { m: null, s: "Approved", v: "₹ 5,610 million" }, "a project budget under New Businesses carries no capex measure");
    const all = MEASURES.map((m) => MLABEL[m]); eq(new Set(all).size, all.length, "the nine measure labels are all different"); for (const a of all) for (const b of all) if (a !== b) ok(!a.includes(b) || b === "Order book" && a === "Order book", "label '" + b + "' is not part of '" + a + "'");
    for (const t of ["BOOK"]) ok(!c.orders.body.includes(">" + t + "<"), "an order book is never shown among orders");
    ok(!c.order_book.body.includes("INFLOW") && !c.order_book.body.includes("TCV") && !c.order_book.body.includes("ANNOUNCED"), "order inflow, TCV and announced orders never appear under Order Book");
    ok(!c.capex.body.includes(">BUSINESS<"), "a project budget is never shown under Capex"); }
  { // an event that mixes kinds is not shown at all
    const P = load({ spotFor: null }); const doc = { events: [E({ event_type: "order_book", measure: "order_inflow", title: "MIX1" }), E({ event_type: "order", measure: "order_book", title: "MIX2" }), E({ event_type: "capex", measure: "order_book", title: "MIX3" }), E({ event_type: "capex", measure: "capex_actual", status: "planned", title: "MIX4" }),
      E({ event_type: "capex", measure: "capex_approved", status: "planned", title: "MIX5" }), E({ event_type: "capacity", measure: "capacity_operational", status: "planned", title: "MIX6" }), E({ event_type: "business", measure: "capex_actual", title: "MIX7" }), E({ event_type: "order", measure: "deal_value_tcv", status: "completed", title: "MIX8" }), E({ event_type: "order", measure: "order_announced", title: "FINE" })] };
    const h = P.G.render("TCS", doc); for (let i = 1; i <= 8; i++) no(h, new RegExp("MIX" + i), "MIX" + i + " (kinds mixed) is not shown"); re(h, />FINE</, "a clean event beside them is shown"); }
  // the real data: the same rules
  for (const e of EVENTS) { if (e.event_type === "order_book") eq(e.measure, "order_book", e.title + ": an order-book event is measured as order book"); if (e.measure === "order_book") eq(e.event_type, "order_book", e.title + ": only order-book events use that measure"); if (e.measure === "deal_value_tcv") ok(e.event_type === "order", e.title + ": TCV is an order measure"); if (e.event_type === "business") eq(e.measure, null, e.title + ": a new-business event is not a capex measure"); }
  ok(EVENTS.some((e) => e.measure === "deal_value_tcv") && EVENTS.some((e) => e.measure === "order_inflow") && EVENTS.some((e) => e.measure === "order_book"), "the data holds TCV, inflow and a real order book as separate records");
  { const lt = EVENTS.filter((e) => e.symbol === "LT"); eq(lt.map((e) => [e.event_type, e.measure, e.value]).sort(), [["order", "order_inflow", 4356], ["order_book", "order_book", 7403]], "L&T: inflow 4356 and order book 7403 are two records, each in its own kind"); }
  { const ms = EVENTS.filter((e) => e.symbol === "MARUTI"); ok(ms.some((e) => e.event_type === "business" && e.value === 5610 && e.measure === null), "Maruti's CBG project budget is a new-business record, not capex"); ok(!ms.some((e) => e.event_type === "capex"), "and Maruti has no capex record from that budget"); ok(ms.some((e) => e.event_type === "capacity" && e.measure === "capacity_operational" && e.status === "completed"), "the commissioned plant is operational capacity"); }
  ok(EVENTS.filter((e) => e.event_type === "acquisition").every((e) => e.status === "announced"), "every acquisition in the data is announced, none is called completed (no source says so)");
  ok(EVENTS.filter((e) => e.measure === "capex_actual").every((e) => /capex|capital expenditure/i.test(e.evidence_quote)), "actual capex rests on a quote that says capex");

  // ================= 3. source safety (the data as shipped) =================
  ok(EVENTS.length >= 10 && EVENTS.length < 60, "a small curated file: " + EVENTS.length + " events");
  const seen = new Set();
  for (const e of EVENTS) {
    const who = e.symbol + " " + e.date + " " + e.title;
    ok(G.valid(e), who + ": passes the rules of the module (so it is shown)");
    ok(TEST_STOCKS.includes(e.symbol), who + ": a test-universe stock");
    for (const f of ["symbol", "date", "event_type", "title", "source_name", "source_url", "retrieved_on", "evidence_quote"]) ok(typeof e[f] === "string" && e[f].trim() !== "", who + ": " + f + " is present");
    const u = new URL(e.source_url); eq(u.protocol, "https:", who + ": https source"); ok(OFFICIAL_HOSTS.includes(u.hostname), who + ": host " + u.hostname + " is one of the official hosts approved in this test"); no(e.source_url, AGGREGATOR, who + ": not a media / aggregator / data-vendor URL"); ok(!/\s/.test(e.source_url) && e.source_url === e.source_url.trim(), who + ": the URL has no spaces or padding");
    no(e.source_name + " " + u.hostname, AGGREGATOR, who + ": source name is not a media / aggregator / data vendor"); no(e.source_name, /\b(analyst|broker|brokerage|research|estimate|forecast|consensus|news)\b/i, who + ": source name is not an analyst, broker or news source"); ok(/\((company|company filing on NSE)\)$/.test(e.source_name), who + ": source name says whose document it is");
    ok(e.evidence_quote.length >= 12 && e.evidence_quote.length < 600, who + ": the quote is a real sentence or line"); ok(!/\.\.\.|…/.test(e.evidence_quote), who + ": the quote is not elided"); ok(e.evidence_quote === e.evidence_quote.trim() && !/\s{2,}/.test(e.evidence_quote), who + ": the quote has plain spacing");
    ok(e.retrieved_on >= e.date, who + ": retrieved on or after the document date"); ok(e.source_date === null || e.source_date === e.date, who + ": source_date is the document date or null");
    if (e.value !== null) { ok(typeof e.value === "number" && e.value > 0, who + ": a positive number"); const digits = String(e.value); ok(e.evidence_quote.replace(/,/g, "").includes(digits), who + ": the stored value " + digits + " appears in the quote"); ok(e.currency && e.unit, who + ": has currency and unit"); }
    else eq([e.currency, e.unit], [null, null], who + ": no value, so no currency or unit");
    ok(e.value !== 0, who + ": zero is never used");
    ok(!/https?:|www\./i.test(e.evidence_quote + e.title + (e.description || "")), who + ": no web address in the text fields");
    no([e.title, e.description || "", e.segment || "", e.source_name, e.evidence_quote].join(" "), ADVICE, who + ": no advice, rating, score or opinion wording in the data");
    const k = [e.symbol, e.date, e.event_type, e.measure, e.title, e.source_url].join("|"); ok(!seen.has(k), who + ": not a duplicate"); seen.add(k);
    // what the quote must say for the label we give it
    const q = e.evidence_quote;
    if (e.measure === "order_book") re(q, /order book/i, who + ": order-book label needs the words 'order book' in the quote");
    if (e.measure === "order_inflow") re(q, /order inflow/i, who + ": order-inflow label needs the words 'order inflow'");
    if (e.measure === "deal_value_tcv") re(q, /TCV|total contract value/i, who + ": TCV label needs TCV in the quote");
    if (e.measure === "capex_actual") re(q, /capex|capital expenditure/i, who + ": capex label needs capex in the quote");
    if (e.measure === "capex_approved" || e.status === "approved") re(q, /approved/i, who + ": 'approved' needs the word in the quote");
    if (e.measure === "capex_planned" || e.status === "planned") re(q, /\b(will|plan\w*|propos\w*)\b/i, who + ": 'planned' needs plan / will in the quote");
    if (e.measure === "capacity_operational" || e.status === "completed") re(q, /commission|commenced|completed|completion|operational/i, who + ": 'completed / operational' needs the source to say so");
    if (e.status === "announced") re(q, /announc|deal|agreement|entered|won|signed/i, who + ": 'announced' needs the source to announce, agree or win something");
    if (e.event_type === "capex") no(q, /\bproject cost\b|\bbudget\b/i, who + ": capex is not built from a project cost or budget"); }
  { const URLS = ["https://assets.airtel.in/static-assets/cms/investor/docs/quarterly_results/2025-26/Q4/Press-Release.pdf", "https://itcportal.com/content/dam/itc-corporate/open-pdfs/investor/quarterly-results/quarterly-results-2024-2025/march-2025/ITC-Press-Release-Q4-FY2025.pdf",
      "https://nsearchives.nseindia.com/corporate/PAM_05052026182003_AnalystPresentationMarch2026.pdf", "https://www.infosys.com/investors/reports-filings/quarterly-results/2025-2026/q2/documents/ifrs-usd-press-release.pdf",
      "https://www.marutisuzuki.com/corporate/media/press-releases/2026/july/maruti-suzuki-announces-financial-results-for-quarter-1", "https://www.ril.com/sites/default/files/2025-04/25042025_Media_Release_RIL_Q4_FY2024_25_Financial_and_Operational_Performance.pdf",
      "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2026-27/q1/IND%20AS/Press%20Release%20-%20INR.pdf"];
    eq([...new Set(EVENTS.map((e) => e.source_url))].sort(), URLS.sort(), "the source URLs are exactly the seven reviewed documents (a new document needs a reviewed addition here)");
    const COUNTS = { RELIANCE: 2, TCS: 1, INFY: 2, HDFCBANK: 0, ICICIBANK: 0, SBIN: 0, ITC: 2, BHARTIARTL: 3, LT: 2, MARUTI: 2 }; for (const s of TEST_STOCKS) eq(EVENTS.filter((e) => e.symbol === s).length, COUNTS[s], s + ": the number of reviewed events");
    eq(EVENTS.length, 14, "14 reviewed events in all");
    const KINDS = { order: 3, order_book: 1, capex: 2, capacity: 2, acquisition: 2, business: 4 }; for (const t of TYPES) eq(EVENTS.filter((e) => e.event_type === t).length, KINDS[t], t + ": the number of reviewed events of this kind"); }
  ok(!/NaN|Infinity|undefined/.test(RAW), "the file has no NaN / Infinity / undefined"); ok(RAW.endsWith("\n") && !RAW.includes("\t"), "the file is plain two-space JSON");
  no(RAW, /api\.upstox|token|secret|apikey|api_key|bearer|authorization|password/i, "the data holds no token, key or secret");
  { const SAMPLE = ["SBIN", "HDFCBANK", "ICICIBANK"]; for (const s of SAMPLE) eq(EVENTS.filter((e) => e.symbol === s).length, 0, s + ": no verified entry yet, and none is invented"); }
  { // the stocks with entries have the entries the documents support; a few anchors against the documents
    const by = (s, t) => EVENTS.filter((e) => e.symbol === s && e.event_type === t);
    eq(by("RELIANCE", "capex").map((e) => [e.value, e.currency, e.unit, e.measure, e.status]), [[131107, "INR", "crore", "capex_actual", null]], "RELIANCE: FY25 capex 131,107 crore, actual");
    eq(by("TCS", "order").map((e) => [e.measure, e.value, e.unit]).sort(), [["order_announced", 800, "million"]], "TCS: only the SKF deal win, 800 million (the quarterly TCV record was removed: the release itself calls it both TCV and order book)");
    eq(by("INFY", "acquisition").map((e) => [e.status, e.value]), [["announced", null]], "INFY: Telstra / Versent is announced, no value given");
    eq(by("BHARTIARTL", "capex").map((e) => [e.value, e.measure, e.segment]), [[16066, "capex_actual", "Consolidated"]], "BHARTIARTL: consolidated Q4 capex 16,066 crore, actual");
    eq(by("BHARTIARTL", "business").map((e) => [e.status, e.value]).sort(), [["announced", 1], ["planned", 20000]], "BHARTIARTL: Nxtra investment announced, NBFC capitalisation planned");
    eq(by("ITC", "acquisition").map((e) => e.status), ["announced"], "ITC: the Century Pulp and Paper agreement is announced / agreed, not completed (the bundled FMCG record was removed)"); }

  // ================= 4. the page =================
  { // dashboard: nothing, and no request
    for (const hash of ["", "#", "#tab=scans", "#compare=TCS,INFY", "#stock=", "#stock=%E0", "#stock=bad sym", "#stock=TCS&x=1", "#stock=TCS,INFY", "#stock=-", "#stock=%20"]) for (const sp of [null, "TCS", "BAD SYM", "bad sym", "-", ""]) { const P = load({ hash, spotFor: sp }); P.notify(); await sleep(8); eq([P.fetched.length, P.styles.length], [0, 0], "no Stock Detail route (" + hash + ") with spot " + JSON.stringify(sp) + ": no request, no style"); }
    const P = load({ hash: "", spotFor: "TCS" }); P.notify(); await sleep(15); eq([P.fetched.length, P.state()], [0, null], "a spot on a page that is not Stock Detail is left alone and nothing is fetched");
    const Q = load({ hash: "#stock=TCS", spotFor: null }); Q.notify(); await sleep(15); eq(Q.fetched.length, 0, "Stock Detail without the snapshot spot: no request"); eq(Q.styles.length, 0, "and no style"); }
  { const P = load(); P.notify(); await sleep(20); eq(P.fetched.map((f) => f.u), ["out/growth_events.json"], "exactly one request, to the static file, relative like the page's other files"); eq(P.fetched[0].o, { cache: "no-store" }, "with the page's usual no-store option, no headers");
    eq(P.state(), "done", "the spot is filled"); eq(P.styles.length, 1, "one style element"); eq(P.styles[0].id, "growthStyle", "named growthStyle"); P.notify(); P.notify(); await sleep(15); eq([P.fetched.length, P.styles.length], [1, 1], "more mutations (its own fill included) change nothing"); }
  { const P = load({ spotFor: null }); const sp = P.place("TCS", { notify: false }); let writes = 0, inner = sp.innerHTML; Object.defineProperty(sp, "innerHTML", { get: () => inner, set: (v) => { writes++; inner = v; } }); P.notify(); await sleep(20); eq(writes, 1, "the spot is written once"); P.notify(); P.notify(); await sleep(15); eq(writes, 1, "and not again when the observer fires for that very write (no loop)"); }
  { const P = load(); eq(P.obs.length, 1, "one observer"); eq(P.obs[0].opts, { childList: true, subtree: true }, "on the Stock Detail area, children and subtree (the snapshot is written into a child)"); }
  { // the six categories
    const P = load(); P.notify(); await sleep(20); const c = cats(P.html());
    eq((P.html().match(/data-gr="([a-z_]+)"/g) || []).map((x) => x.slice(9, -1)), CATS.map((x) => x[0]), "six categories, in the specified order in the page"); eq((P.html().match(/<h5>([^<]*)<\/h5>/g) || []).map((x) => x.slice(4, -5)), CATS.map((x) => x[1]), "with the specified names, in that order"); eq(Object.values(c).map((x) => x && x.title), CATS.map((x) => x[1]), "with the specified names");
    eq(events(c.orders.body).length, 1, "TCS has one order event"); for (const k of ["order_book", "capex", "capacity", "acquisitions", "business"]) { eq(events(c[k].body).length, 0, "TCS " + k + ": no event"); eq(c[k].body, '<p class="gr-u">Unavailable</p>', "TCS " + k + ": says Unavailable, and nothing else"); }
    eq((P.html().match(/gr-u">Unavailable</g) || []).length, 5, "five categories say Unavailable"); const t = un(P.html());
    no(t, /\bno (orders?|order book|capex|capacity|acquisitions?|growth|business(es)?|events?)\b/i, "it never says 'no orders / no capex / no acquisition'"); no(t, /\bthere (are|were) no\b/i, "and never claims absence"); no(P.html(), /<p class="gr-u">-<\/p>|gr-e">-</, "no fake '-' events");
    re(t, /Unavailable means there is no verified entry in this dataset; it does not mean that nothing has happened\./, "the note explains what Unavailable means"); }
  { // an event, in full
    const P = load({ spotFor: "TCS" }); P.notify(); await sleep(20); const c = cats(P.html()); const ev = events(c.orders.body); eq(ev.length, 1, "one event"); const skf = EVENTS.find((e) => e.symbol === "TCS" && e.measure === "order_announced");
    const sk = ev.find((x) => x.includes(">" + skf.title + "<")); ok(sk, "the SKF event is there");
    re(sk, /<span class="gr-l">Order announced<\/span><span class="gr-s">Announced<\/span><b class="gr-v">US\$ 800 million<\/b>/, "measure, status and value in order");
    for (const [x, e] of [[sk, skf]]) { re(x, new RegExp('Source: ' + e.source_name.replace(/[()$.*+?^|\\\[\]{}]/g, "\\$&")), "the source name is shown"); const m = /<a href="([^"]*)" target="_blank" rel="noopener noreferrer">View source<\/a>/.exec(x); ok(m, "a 'View source' link, opening in a new tab without opener"); eq(unesc(m[1]), e.source_url, "the link is exactly the stored URL"); re(x, /<details class="gr-q"><summary>Quote from the source<\/summary><blockquote>/, "the quote can be opened"); ok(unesc(x).includes("<blockquote>" + e.evidence_quote + "</blockquote>"), "and is the stored quote, unchanged"); }
    ok(sk.includes(skf.description) || sk.includes(un(skf.description)), "the description is shown"); re(sk, /<span class="gr-d">Date: 2026-07-09<\/span>/, "date"); re(un(sk), /Deal win with SKF/, "the title says it is a deal win"); no(un(sk), /Total Contract Value|order book/i, "and it is not presented as a TCV or an order book"); }
  { // every real stock shows its own events and only those
    for (const s of TEST_STOCKS) { const P = load({ spotFor: s, hash: "#stock=" + s }); P.notify(); await sleep(15); const h = P.html(), mine = EVENTS.filter((e) => e.symbol === s), c = cats(h);
      const shown = []; for (const k of Object.keys(c)) for (const x of events(c[k].body)) shown.push(unesc(/<p class="gr-t">([\s\S]*?)<\/p>/.exec(x)[1]));
      eq(shown.sort(), mine.map((e) => e.title).sort(), s + ": exactly its own events are shown");
      for (const o of EVENTS.filter((e) => e.symbol !== s)) no(unesc(h), new RegExp(o.title.replace(/[()$.*+?^|\\\[\]{}]/g, "\\$&") + "<"), s + ": the event of " + o.symbol + " ('" + o.title + "') is not shown");
      eq((h.match(/<a href=/g) || []).length, mine.length, s + ": one source link per event");
      for (const e of mine) { const cat = CATS.find((x) => x[2] === e.event_type)[0]; ok(c[cat].body.includes(">" + e.title.replace(/&/g, "&amp;") + "<") || unesc(c[cat].body).includes(">" + e.title + "<"), s + ": '" + e.title + "' is under " + cat); }
      for (const a of h.match(/href="[^"]*"/g) || []) ok(mine.some((e) => unesc(a.slice(6, -1)) === e.source_url), s + ": link " + a.slice(0, 60) + " is a stored URL of this stock"); } }
  { // a different stock's spot, and a stale answer
    const P = load({ spotFor: "LT", hash: "#stock=LT", delay: 25 }); P.notify(); P.place("TCS", { notify: false }); window.location.hash = "#stock=TCS"; P.notify(); await sleep(70); eq(P.fetched.length, 1, "navigating between stocks does not fetch again"); const h = P.html(); re(un(h), /Deal win with SKF/, "the new stock's own events"); no(un(h), /Order inflow/, "and none of the previous stock's");
    const Q = load({ spotFor: "LT", hash: "#stock=LT", delay: 40 }); Q.notify(); const old = Q.spot(); window.location.hash = "#stock=TCS"; Q.place("TCS", { notify: false }); await sleep(90); eq(old.innerHTML, '<p class="snap-t">Unavailable</p>', "an answer that arrives after the spot was replaced is not written into the old spot");
    const R = load({ spotFor: "LT", hash: "#stock=LT", delay: 40 }); R.notify(); window.location.hash = "#stock=TCS"; await sleep(90); eq(R.html(), '<p class="snap-t">Unavailable</p>', "an answer for a stock that is no longer open is dropped");
    const S2 = load({ spotFor: "TCS", hash: "#stock=INFY" }); S2.notify(); await sleep(20); eq([S2.fetched.length, S2.state()], [0, null], "a spot that names another stock than the open one is left alone");
    const T2 = load({ spotFor: "TCS", hash: "#stock=tcs" }); T2.notify(); await sleep(20); eq(T2.state(), "done", "a lower-case route (the page upper-cases it) still finds the stock"); }
  { // nothing to show
    const bad = ["not json", null, 5, [], {}, { events: null }, { events: {} }, { events: "x" }];
    for (const doc of bad) { const P = load({ doc, spotFor: "TCS" }); P.notify(); await sleep(15); eq(P.html(), '<p class="snap-t">Unavailable</p>', "a file of the wrong shape (" + JSON.stringify(doc) + "): Unavailable, no events, no error"); }
    const F = load({ failFetch: true }); F.notify(); await sleep(20); eq(F.html(), '<p class="snap-t">Unavailable</p>', "a failed request: Unavailable"); const N = load({ status: 404 }); N.notify(); await sleep(20); eq(N.html(), '<p class="snap-t">Unavailable</p>', "a missing file (404): Unavailable");
    const E0 = load({ doc: { events: [] } }); E0.notify(); await sleep(20); eq((E0.html().match(/gr-u">Unavailable</g) || []).length, 6, "an empty file: all six categories say Unavailable"); }
  { // bad entries are dropped one by one, duplicates once, order is fixed
    const good = E({ title: "GOOD", measure: "order_announced" }); const doc = { events: [good, E({ title: "NOQUOTE", evidence_quote: "" }), E({ title: "NOURL", source_url: "" }), E({ title: "BADTYPE", event_type: "guidance" }), E({ title: "ZERO", value: 0, currency: "INR", unit: "crore" }), E({ title: "OTHER", symbol: "INFY" }), good, Object.assign({}, good)] };
    const P = load({ doc }); const list = P.G.forSymbol(doc, "TCS"); eq(list.map((e) => e.title), ["GOOD"], "only the clean, own-stock event remains, once"); eq(P.G.forSymbol(doc, "tcs"), [], "symbols match exactly");
    const d2 = { events: [E({ title: "B", date: "2026-01-01" }), E({ title: "A", date: "2026-01-01" }), E({ title: "C", date: "2026-03-01" }), E({ title: "A", date: "2026-01-01", source_url: "https://www.tcs.com/a.pdf" })] };
    eq(P.G.forSymbol(d2, "TCS").map((e) => e.title + e.date + e.source_url.slice(-5)), ["C2026-03-01x.pdf", "A2026-01-01a.pdf", "A2026-01-01x.pdf", "B2026-01-01x.pdf"].map((x) => x), "newest first, then by title, then by URL: a fixed order");
    eq(P.G.forSymbol({ events: d2.events.slice().reverse() }, "TCS").map((e) => e.title + e.date), P.G.forSymbol(d2, "TCS").map((e) => e.title + e.date), "the order does not depend on the order in the file");
    ok(P.G.forSymbol({ events: [good, good, good] }, "TCS").length === 1, "the same event three times is one");
    ok(P.G.forSymbol({ events: [good, E({ title: "GOOD", source_url: "https://www.tcs.com/other.pdf" })] }, "TCS").length === 2, "the same title from two different sources is two events");
    eq(P.G.forSymbol({ events: [good, E({ title: "GOOD", measure: "order_inflow" })] }, "TCS").map((e) => e.measure).sort(), ["order_announced", "order_inflow"], "the same title with two different measures is two events (kinds are never merged)");
    eq(P.G.forSymbol({ events: [good, E({ title: "GOOD", event_type: "business", measure: null })] }, "TCS").length, 2, "the same title in two kinds is two events"); eq(P.G.forSymbol({ events: [good, E({ title: "GOOD", date: "2026-07-10" })] }, "TCS").length, 2, "the same title on two dates is two events");
    const h = P.G.render("TCS", { events: [good, good] }); eq((h.match(/class="gr-e"/g) || []).length, 1, "a duplicate is shown once"); }
  { // text is escaped, never markup
    const P = load({ spotFor: null }); const evil = E({ title: '<img src=x onerror=alert(1)>', description: '<script>alert(2)</script>', segment: '"><b>x', source_name: "<i>Co</i> & Sons", source_url: "https://www.tcs.com/a?b=1&c=2", evidence_quote: "<b>q</b> \"quoted\" & more", value: 5, currency: "<x>", unit: "<y>" });
    const h = P.G.render("TCS", { events: [evil] }); no(h, /<img|<script|<i>|<b>x|<b>q|<x>|<y>/, "no markup from the data reaches the page"); re(h, /&lt;img src=x onerror=alert\(1\)&gt;/, "the title is shown as text"); re(h, /href="https:\/\/www\.tcs\.com\/a\?b=1&amp;c=2"/, "the URL is escaped inside the attribute but is the stored URL"); }
  { // number format
    const f = load({ spotFor: null }).G.fmtValue;
    eq(f({ value: 131107, currency: "INR", unit: "crore" }), "₹ 131,107 crore", "INR crore"); eq(f({ value: 4356, currency: "INR", unit: "billion" }), "₹ 4,356 billion", "INR billion"); eq(f({ value: 9.5, currency: "USD", unit: "billion" }), "US$ 9.5 billion", "USD with a decimal");
    eq(f({ value: 1234567.25, currency: "USD", unit: "million" }), "US$ 1,234,567.25 million", "thousands separators and decimals"); eq(f({ value: 5610, currency: "INR", unit: "million" }), "₹ 5,610 million", "INR million"); eq(f({ value: 12, currency: "EUR", unit: "million" }), "EUR 12 million", "another currency is shown by its code"); eq(f({ value: null }), null, "no value, no text"); eq(f({}), null, "no value field, no text"); eq(f({ value: undefined, currency: "INR", unit: "crore" }), null, "undefined is not a value"); }
  { // the real Investor Snapshot hands over the spot, and only on its own Future Growth Evidence group
    const els = {}; global.MutationObserver = undefined; global.window = { location: { hash: "" }, addEventListener() {}, scrollTo() {} };
    global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, head: { appendChild() {} }, getElementById: () => null, addEventListener() {}, querySelector: () => null, createElement: () => ({ setAttribute() {} }) };
    global.fetch = async () => ({ ok: false, json: async () => null }); (0, eval)(techCode); (0, eval)(researchCode); (0, eval)(fhCode); (0, eval)(cmpCode); (0, eval)(snapCode);
    const h = window.SLSnapshot.build("TCS", {}), g = /<div class="snap-g wide" data-snap="future">([\s\S]*?)<\/div><div class="snap-g"/.exec(h);
    ok(g, "the Future Growth Evidence group is there, full width"); re(g[1], /<h4>Future Growth Evidence<\/h4>/, "with its title"); no(g[1], /View details/, "without a View details link (nothing deeper to open)"); re(g[1], /<div data-growth-for="TCS"><p class="snap-t">Unavailable<\/p>/, "holding the spot for TCS, which says Unavailable until the growth module fills it");
    eq((h.match(/data-growth-for=/g) || []).length, 1, "exactly one spot"); const x = window.SLSnapshot.build("M&M", {}); re(x, /data-growth-for="M&amp;M"/, "the spot's stock name is escaped"); no(g[1], /will be added through the News/, "the old placeholder sentence is gone from the group");
    re(h, /data-snap="ownership"[\s\S]*Shareholding history will be added/, "the other placeholders are untouched"); re(h, /data-snap="developments"[\s\S]*corporate developments will be added through the News &amp; Announcements layer/, "Recent Developments is untouched (a later phase)"); }

  // ================= 5. the rest of the page is exactly as it was =================
  { const code = grCode.replace(/\/\*[\s\S]*?\*\//g, "");
    no(code, /SLSearch|SLCompare|SLFinHistory|SLTech|SLResearch|SLSnapshot|companySearch|stockSearch|detailChart|detailTech|detailResearch|compareView|finHistory/, "the growth module touches no other feature: search, comparison, Financial History, charts, technicals, research");
    no(code, /location\.hash\s*=|addEventListener|replaceState|pushState|\.scrollTo|scrollIntoView|localStorage|sessionStorage|indexedDB|setInterval|setTimeout|requestAnimationFrame/, "it sets no route, listens to no event, scrolls nothing, stores nothing and runs no timer");
    eq((code.match(/createElement\("/g) || []).length, 1, "the only element it creates is its style"); re(code, /createElement\("style"\)/, "(a style element)"); eq((code.match(/\.innerHTML\s*=/g) || []).length, 1, "and the only place it writes is the spot");
    eq((code.match(/\$\("[^"]*"\)/g) || []).sort(), ['$("detail")', '$("detail")', '$("growthStyle")'], "the only ids it looks up are the Stock Detail area and its own style"); no(code, /document\.body|document\.querySelector\(|document\.addEventListener/, "it never reaches outside the Stock Detail area");
    eq(Object.keys(load({ spotFor: null }).G).sort(), ["MEASURES", "STATUSES", "TYPES", "fill", "fmtValue", "forSymbol", "render", "valid"], "its public surface is small and fixed"); }
  { const keep = ['id="rows"', 'id="tabs"', 'id="detail"', 'id="demo"', 'id="stat"']; for (const id of keep) ok(html.includes(id), "the dashboard still has " + id);
    no(html.replace(/<script type="module" id="stocklens-snapshot">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-growth">[\s\S]*?<\/script>\n/, ""), /data-growth-for|Future Growth Evidence/, "no growth markup in the static page or the dashboard: only the snapshot and the growth module mention it");
    ok(html.includes('id="stocklens-search"') && html.includes('id="stocklens-compare"') && html.includes('id="stocklens-snapshot"'), "search, comparison and snapshot modules are still there"); }

  // ================= 6. security =================
  { const code = grCode.replace(/\/\*[\s\S]*?\*\//g, "");
    eq((code.match(/\bfetch\(/g) || []).length, 1, "one fetch call in the module"); eq(code.match(/fetch\("([^"]*)"/)[1], "out/growth_events.json", "and it fetches only the static growth file");
    eq((code.match(/"[^"]*\.json"/g) || []), ['"out/growth_events.json"'], "the only file name in the code is growth_events.json");
    no(code, /https?:\/\//, "no web address in the code"); no(code, /upstox|nseindia|bseindia|api\.|\/api\/|graphql|websocket|XMLHttpRequest|sendBeacon|EventSource|importScripts|import\(/i, "no Upstox / NSE / BSE / API / socket / beacon / dynamic import");
    no(code, /token|secret|apikey|api_key|bearer|authorization|password|credential|cookie|headers/i, "no token, key, header or cookie"); no(code, /\beval\s*\(|new Function|document\.write|outerHTML|insertAdjacentHTML|javascript:/, "no code evaluation or unsafe markup insertion");
    eq((code.match(/\.innerHTML\s*=/g) || []).length, 1, "one innerHTML write, of escaped text"); ok(/rel="noopener noreferrer"/.test(code), "links open without an opener"); }
  { no(snapCode, /growth_events/, "the snapshot module does not load the growth file"); no(srchCode, /growth_events/, "nor does the search module"); no(html.replace(grCode, ""), /growth_events/, "no other part of the page mentions the growth file");
    for (const f of [".github/workflows/update.yml", ".github/workflows/financial_history.yml", ".github/workflows/historical.yml", ".github/workflows/probe_financials.yml", ".github/workflows/save_ledger.yml", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "historical_updater.py", "validate_outputs.py"]) no(fs.readFileSync(ROOT + "/" + f, "utf8"), /growth_events/, f + " does not know about the growth file"); }
  { const st = cp.execSync("git status --porcelain", { cwd: ROOT, encoding: "utf8" }).split("\n").filter(Boolean).map((l) => l.slice(3));
    for (const f of st) ok(!/^(\.github\/|nse_updater|fundamentals_updater|financials_updater|financial_history_updater|historical_updater|validate_outputs|official_financial_records|company_profiles\.json)/.test(f), "no protected file is changed: " + f); }

  // ================= 7. wording =================
  { const all = []; for (const s of TEST_STOCKS.concat(["ZZZ"])) { const P = load({ spotFor: s, hash: "#stock=" + s }); P.notify(); await sleep(10); all.push(P.html()); } const t = un(all.join(" "));
    no(t, ADVICE, "the whole section, for every stock, contains no advice, rating, score, opinion or forecast wording"); no(t, /\b(positive|negative|strong|weak|good|bad|attractive|likely to rise|future return|beat|miss|upgrade|downgrade|outlook|guidance|estimate|forecast|expect(ed|s)?)\b/i, "and none of the further words the specification names, nor guidance or estimates");
    no(unesc(un(all.join(" "))), /\bBuy\b|\bSell\b|\bTarget\b|\bRating\b|\bScore\b/, "none of Buy / Sell / Target / Rating / Score, in any case");
    const code = grCode.replace(/\/\*[\s\S]*?\*\//g, ""); no((code.match(/"[^"]*"|'[^']*'/g) || []).filter((m) => /[a-z]{4,}\s[a-z]{3,}/i.test(m)).join(" "), ADVICE, "and no such word in the module's own sentences");
    re(un(all[0]), /Facts from official company documents/, "the section says what it is: facts with sources"); }

  // ================= 8. layout (the rules that keep it inside a 320px screen) =================
  { const m = /var STYLE='([\s\S]*?)';\nfunction addStyle/.exec(grCode.replace(/'\n \+'/g, "")); ok(m, "the style text is found"); const css = m[1];
    const rules = css.split("}").filter(Boolean).map((r) => r.trim() + "}"); ok(rules.every((r) => r.startsWith("#detailSnapshot ")), "every rule is scoped to the snapshot, so nothing else on the page is restyled");
    re(css, /\.gr-grid\{display:grid;grid-template-columns:repeat\(auto-fit,minmax\(min\(260px,100%\),1fr\)\)/, "the categories sit in a grid that gives way to one column and never needs more than the screen"); no(css, /position:\s*(absolute|fixed)|white-space:\s*nowrap|min-width:\s*[1-9]|(^|[;{])width:\s*\d+px|overflow-x:\s*(scroll|auto)/, "no absolute or fixed position, no nowrap, no fixed width, no scrolling box");
    for (const cls of [".gr-t", ".gr-x", ".gr-src", ".gr-q blockquote"]) re(css, new RegExp(cls.replace(/[.]/g, "\\.") + "\\{[^}]*overflow-wrap:anywhere"), cls + " wraps long words");
    re(css, /\.gr-src a\{overflow-wrap:anywhere;word-break:break-all\}/, "a long URL text wraps"); re(css, /\.gr-m\{display:flex;flex-wrap:wrap/, "the meta line wraps"); re(css, /\.gr-m>\*\{min-width:0;overflow-wrap:anywhere\}/, "and so do its parts"); re(css, /\.gr-c\{min-width:0/, "a category may shrink"); re(css, /\.gr-e\{min-width:0/, "an event may shrink");
    ok((css.match(/#[0-9a-f]{3,8}\b/gi) || []).length === 0, "no hard-coded colour: it follows the page's light and dark colours"); re(css, /var\(--line\)/, "it uses the page's own variables"); }
  { const P = load({ spotFor: null }); const long = "W".repeat(300); const h = P.G.render("TCS", { events: [E({ title: long, description: long, segment: long, source_name: long + " (company)", source_url: "https://www.tcs.com/" + "a".repeat(300), evidence_quote: long, value: 123456789012, currency: "INR", unit: long })] });
    ok(h.includes(long), "a very long text is kept whole (it wraps by CSS, it is not cut)"); eq((h.match(/class="gr-e"/g) || []).length, 1, "and still one event"); no(h, /style="/, "no inline style in the markup"); }

  console.log("Growth evidence tests passed (" + checks + " checks)");
})().catch((e) => { console.error("GROWTH EVIDENCE TEST FAILED:", e && e.message ? e.message : e); if (e && e.stack) console.error(e.stack.split("\n").slice(1, 4).join("\n")); process.exit(1); });
