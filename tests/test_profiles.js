// Run: node tests/test_profiles.js   (no network, no browser)
// Tests Phase 4 Step 3 - Business Profile: (A) the shipped company_profiles.json (sources, dates, https, wording),
// (B) how Stock Research and the Investment Checklist use it (Available / Needs review / Unavailable, name/sector precedence,
// https-only links, escaping), (C) end-to-end placement with the fourth data file, (D) protection of everything else.
const fs = require("fs"), assert = require("assert"), crypto = require("crypto"), path = require("path");
const ROOT = path.join(__dirname, "..");
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const BANNED = /\b(buy|sell|strong|bullish|bearish|signals?|rating|score|target|undervalued|overvalued|recommend\w*|tally|composite|overall)\b/i;
const isDate = (x) => typeof x === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x) && new Date(x + "T00:00:00Z").toISOString().slice(0, 10) === x;

const html = read("index.html");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const has = (m) => blocks.find((b) => b.includes(m));
const researchCode = has("Phase 4 Step 1 - Stock Research"), checkCode = has("Phase 4 Step 2 - Investment Checklist"),
      detailCode = has("Stock Detail view"), chartCode = has("Phase 3 Step 2 - historical price chart"), techCode = has("Phase 3 Step 3 - Technical Snapshot");

// ---------- the real modules on a shared window (pure API) ----------
function loadModules(htmlText) {
  const bl = htmlText.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
  const f = (m) => bl.find((b) => b.includes(m));
  global.window = { location: { hash: "" } }; global.document = { getElementById: () => null }; global.fetch = async () => ({ ok: false }); delete global.MutationObserver;
  new Function(f("Phase 3 Step 3 - Technical Snapshot"))(); new Function(f("Phase 3 Step 2 - historical price chart"))();
  new Function(f("Phase 4 Step 1 - Stock Research"))(); new Function(f("Phase 4 Step 2 - Investment Checklist"))();
  return { R: window.SLResearch, C: window.SLChecklist };
}
const day = (start, i) => { const d = new Date(start + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
const candles = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i, volume: 1000 + i }));
const bench = () => Array.from({ length: 400 }, (_, i) => ({ date: day("2025-01-01", i), open: 20000 + i, high: 20001 + i, low: 19999 + i, close: 20000 + i, volume: 0 }));
const HIST = { updated: "2026-10-01", source: "Upstox", benchmark: { symbol: "NIFTY 50", candles: bench() }, stocks: { TCS: { symbol: "TCS", candles: candles() } } };
const FUND = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "Tata Consultancy Services Ltd", sector: "IT Services", pe: 25.5, pb: 12.3, roa: 20.1, roe: 51.2, roce: 60.4, ev_ebitda: 18.7 },
  { symbol: "INFY", company_name: "Infosys & Co <Ltd>", sector: null, pe: "abc", pb: 0, roa: null, roe: null, roce: null, ev_ebitda: null },
  { symbol: "HDFCBANK", company_name: "HDFC Bank Ltd", sector: "Financial Services", pe: 18.2, pb: 2.9, roa: 1.8, roe: 16.4, roce: null, ev_ebitda: null }] };
const FIN = { as_of: "2026-10-01", source: "Upstox", stocks: [
  { symbol: "TCS", company_name: "TCS (financials name)", basis: "consolidated", income: { period: "Mar 2026", basis: "consolidated", revenue: 250000.5, total_revenue: 255000.25, profit_after_tax: 48000.75 },
    balance_sheet: { period: "Mar 2026", basis: "consolidated", total_assets: 150000, total_liabilities: 60000, total_equity: 90000, liabilities_to_equity: 0.6667 }, cash_flow: { period: "Mar 2026", basis: "consolidated", operating: 55000, investing: -20000, financing: -30000 } }] };
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const tile = (h, label) => { const m = h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + escRe(label) + "</span></div>")); return m ? m[1] : "NOT FOUND"; };

// ---------- golden render of the NO-PROFILE case (hash of the Phase 4 Step 2 output at d0ab279; one changed phrase normalised) ----------
function golden(htmlText) {
  const { R, C } = loadModules(htmlText), out = [];
  ["TCS", "INFY", "NONAME", "NOSUCH"].forEach((s) => { const d = { val: FUND, fin: FIN, hist: HIST, prof: null }; out.push(R.build(s, d), C.build(s, d)); });
  out.push(R.build("TCS", { val: null, fin: null, hist: null }), C.build("TCS", { val: null, fin: null, hist: null }));
  return out.join("\n=====\n").split("no verified business profile for this stock").join("no business description exists in the current data files");
}
if (process.argv[2] === "--print-golden") { console.log(sha(golden(fs.readFileSync(process.argv[3], "utf8")))); process.exit(0); }
const GOLDEN = "cdae1bbf378a2b58e10028459f12e9e277819819d0784bbd150ec8cf76bf8710";

(async () => {
  // ================= A. the shipped file =================
  const raw = read("company_profiles.json"); no(raw, /NaN|Infinity/, "no NaN / Infinity in the file");
  const doc = JSON.parse(raw), list = doc.stocks;
  ok(isDate(doc.as_of), "as_of is a real date"); ok(Array.isArray(list), "stocks is an array");
  eq(list.map((p) => p.symbol).sort(), ["BHARTIARTL", "ICICIBANK", "INFY", "ITC", "LT", "MARUTI", "RELIANCE", "SBIN", "TCS"], "exactly the nine verified symbols");
  no(list.map((p) => p.symbol).join(","), /HDFCBANK/, "HDFCBANK is intentionally absent (not verified)");
  eq(new Set(list.map((p) => p.symbol)).size, list.length, "symbols are unique");
  const HOSTS = { TCS: "www.tcs.com", INFY: "www.infosys.com", RELIANCE: "www.ril.com", ICICIBANK: "www.icici.bank.in", SBIN: "sbi.bank.in", ITC: "www.itcportal.com", BHARTIARTL: "www.airtel.in", LT: "www.larsentoubro.com", MARUTI: "www.marutisuzuki.com" };
  const FIELDS = ["symbol", "company_name", "sector", "description", "key_businesses", "business_model", "source_name", "source_url", "source_date", "retrieved_on", "evidence_quote"];
  list.forEach((p) => {
    eq(Object.keys(p).sort(), FIELDS.slice().sort(), p.symbol + ": exactly the agreed fields");
    ok(typeof p.company_name === "string" && p.company_name.trim(), p.symbol + ": company name");
    ok(typeof p.description === "string" && p.description.trim().length > 20 && p.description.length <= 400, p.symbol + ": concise description (<= 400 characters)");
    ok(typeof p.evidence_quote === "string" && p.evidence_quote.trim().length > 20 && p.evidence_quote.length <= 450, p.symbol + ": evidence quote present and short");
    ok(typeof p.source_name === "string" && p.source_name.trim(), p.symbol + ": source name");
    let u = null; try { u = new URL(p.source_url); } catch (e) { u = null; }
    ok(u && u.protocol === "https:" && u.hostname === HOSTS[p.symbol] && !u.username && !u.password, p.symbol + ": https URL on the official domain " + HOSTS[p.symbol]);
    eq(p.retrieved_on, "2026-10-04", p.symbol + ": retrieved_on");
    ok(p.source_date === null || (isDate(p.source_date) && p.source_date !== p.retrieved_on), p.symbol + ": source_date is null or a real date that is not the retrieval date");
    eq(p.business_model, null, p.symbol + ": business_model is null (no official page states one)");
    eq(p.sector, null, p.symbol + ": sector is null (not stated by the official page; the Upstox sector stays primary)");
    ok(Array.isArray(p.key_businesses) && p.key_businesses.every((k) => typeof k === "string" && k.trim() && k.length <= 80), p.symbol + ": key_businesses is a list of short strings");
    const all = [p.company_name, p.description, p.source_name, p.evidence_quote].concat(p.key_businesses).join(" | ");
    no(all, BANNED, p.symbol + ": no advice / rating wording"); no(all, /[<>]/, p.symbol + ": no markup characters");
  });
  eq(list.find((p) => p.symbol === "INFY").source_date, "2026-09-17", "INFY: the only page that shows a date");
  eq(list.filter((p) => p.source_date === null).length, 8, "the other eight pages show no date -> null");
  no(doc.note + "", BANNED, "file note has no advice wording");
  const wf = read(".github/workflows/update.yml"), cpLines = wf.split("\n").filter((l) => /company_profiles/.test(l));
  eq(cpLines.map((l) => l.trim()), ["cp company_profiles.json _site/out/"], "update.yml: exactly one company_profiles line, the copy");
  ok(wf.indexOf("mkdir _site/out") < wf.indexOf("cp company_profiles.json _site/out/"), "the copy comes after _site/out is created");
  ["nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "historical_updater.py", "validate_outputs.py", ".github/workflows/historical.yml"].forEach((f) => no(read(f), /company_profiles/, f + " does not touch the profiles"));

  // ================= B. behaviour of the real blocks =================
  const { R, C } = loadModules(html);
  const prof = (over) => ({ as_of: "2026-10-04", stocks: [Object.assign({ symbol: "TCS", company_name: "Profile Name Ltd", sector: "Profile Sector", description: "A preferred technology partner.", key_businesses: ["Alpha", "Beta"], business_model: null,
    source_name: "TCS - Who we are", source_url: "https://www.tcs.com/who-we-are", source_date: null, retrieved_on: "2026-10-04", evidence_quote: "q" }, over)] });
  const D = (p) => ({ val: FUND, fin: FIN, hist: HIST, prof: p });
  const bdRow = (p, s = "TCS") => C.sections(s, D(p))[0].rows.find((r) => r.l === "Business description");
  const bdItem = (p, s = "TCS") => R.sections(s, D(p))[0].items.find((i) => i.l === "Business description");
  // valid profile
  let h = R.build("TCS", D(prof({})));
  eq(bdRow(prof({})), { l: "Business description", v: "Profile on file", st: "A", note: "" }, "valid profile -> Available (source_date null is fine)");
  re(h, /<h5[^>]*>Business Profile<\/h5>/, "Business Profile block is shown"); re(h, /A preferred technology partner\./, "description shown");
  re(h, /<li>Alpha<\/li><li>Beta<\/li>/, "key businesses listed"); no(h, /Business model:/, "business_model null -> no business model line");
  re(h, /Source: <a href="https:\/\/www\.tcs\.com\/who-we-are" rel="noopener noreferrer">TCS - Who we are<\/a> · Source date: Not shown · Retrieved: 2026-10-04/, "source line: link, 'Source date: Not shown', retrieval date");
  eq(tile(h, "Business description"), "Profile on file", "tile value");
  ok(h.indexOf("Business Profile") > h.indexOf("Business description") && h.indexOf("Business Profile") < h.indexOf("Fundamental Quality"), "profile block sits inside the Business panel");
  re(R.build("TCS", D(prof({ source_date: "2026-09-17" }))), /Source date: 2026-09-17 · Retrieved: 2026-10-04/, "source_date shown when present");
  re(R.build("TCS", D(prof({ business_model: "Stated model text" }))), /<b>Business model:<\/b> Stated model text/, "business_model shown only when the file has one");
  eq(bdRow(prof({ source_date: undefined })).st, "A", "missing source_date key -> still Available");
  eq(bdRow(prof({ key_businesses: "not a list" })).st, "A", "key_businesses of the wrong type is ignored, not fatal");
  no(R.build("TCS", D(prof({ key_businesses: "not a list" }))), /Key businesses/, "no list shown when key_businesses is not a list");
  re(R.build("TCS", D(prof({ key_businesses: ["Alpha", "", "  ", 5, "Beta"] }))), /<li>Alpha<\/li><li>Beta<\/li><\/ul>/, "blank / non-string key businesses dropped");
  re(R.build("TCS", D(prof({ key_businesses: [] }))), /Business Profile/, "empty key_businesses is fine"); no(R.build("TCS", D(prof({ key_businesses: [] }))), /Key businesses/, "no empty list heading");
  // Checklist block shows the same
  const cp = C.build("TCS", D(prof({})));
  re(text(cp), /Business description Profile on file Available/, "Investment Checklist: Business description Available");
  // name / sector precedence
  eq(tile(h, "Company name"), "Tata Consultancy Services Ltd", "Upstox company name stays primary"); eq(tile(h, "Sector"), "IT Services", "Upstox sector stays primary");
  const noUp = { val: { stocks: [{ symbol: "TCS", company_name: null, sector: null }] }, fin: { stocks: [] }, hist: null, prof: prof({}) };
  eq(tile(R.build("TCS", noUp), "Company name"), "Profile Name Ltd", "profile company name is only a fallback"); eq(tile(R.build("TCS", noUp), "Sector"), "Profile Sector", "profile sector is only a fallback");
  eq(tile(R.build("TCS", { val: { stocks: [{ symbol: "TCS", company_name: null }] }, fin: FIN, hist: null, prof: prof({}) }), "Company name"), "TCS (financials name)", "financials name still beats the profile");
  eq(tile(R.build("TCS", { val: null, fin: null, hist: null, prof: prof({ sector: null }) }), "Sector"), "-", "no sector anywhere -> -");
  eq(tile(R.build("TCS", { val: null, fin: null, hist: null, prof: prof({ source_url: "http://x.com/" }) }), "Company name"), "-", "an invalid profile is not used as a fallback");
  // no profile
  eq(bdRow(null), { l: "Business description", v: "Unavailable", st: "U", note: "no verified business profile for this stock" }, "no profile file -> Unavailable");
  eq(bdRow({ stocks: [] }).st, "U", "empty profile list -> Unavailable"); eq(bdRow(prof({ symbol: "ITC" })).st, "U", "profile for another symbol -> Unavailable");
  ["", 5, "x", [], { stocks: "nope" }, { stocks: [null, 5, "x"] }].forEach((bad, i) => { eq(bdRow(bad).st, "U", "malformed profile document #" + i + " -> Unavailable, no crash"); no(R.build("TCS", D(bad)), /Business Profile/, "malformed document #" + i + ": no profile block"); });
  eq(tile(R.build("TCS", D(null)), "Business description"), "Unavailable", "tile: Unavailable"); no(R.build("TCS", D(null)), /<h5/, "no profile block without a profile");
  const real = JSON.parse(raw);
  eq(bdRow(real, "HDFCBANK").st, "U", "HDFCBANK with the real file -> Unavailable (not in the file)");
  eq(tile(R.build("HDFCBANK", D(real)), "Business description"), "Unavailable", "HDFCBANK tile Unavailable"); no(R.build("HDFCBANK", D(real)), /Business Profile/, "HDFCBANK shows no profile");
  // needs review: profile exists but the source details are incomplete / invalid
  const REVIEW = { "http (not https) URL": { source_url: "http://www.tcs.com/who-we-are" }, "javascript: URL": { source_url: "javascript:alert(1)" }, "data: URL": { source_url: "data:text/html,x" },
    "bare https://": { source_url: "https://" }, "URL with user info": { source_url: "https://user:pw@www.tcs.com/" }, "URL with a quote": { source_url: 'https://www.tcs.com/"onclick="x' }, "URL with a space": { source_url: "https://www.tcs.com/a b" },
    "URL with angle bracket": { source_url: "https://www.tcs.com/<b>" }, "URL with leading space": { source_url: " https://www.tcs.com/" }, "missing URL": { source_url: null }, "numeric URL": { source_url: 5 },
    "protocol-relative URL": { source_url: "//www.tcs.com/" }, "empty description": { description: "" }, "blank description": { description: "   " }, "missing description": { description: null },
    "missing source name": { source_name: "" }, "missing retrieved_on": { retrieved_on: null }, "impossible retrieved_on": { retrieved_on: "2026-02-31" }, "malformed retrieved_on": { retrieved_on: "04-10-2026" },
    "impossible source_date": { source_date: "2026-13-45" }, "numeric source_date": { source_date: 20260917 }, "empty source_date": { source_date: "" } };
  Object.keys(REVIEW).forEach((k) => {
    const o = REVIEW[k], pr = prof(o), r = bdRow(pr), page = R.build("TCS", D(pr)), cpage = C.build("TCS", D(pr));
    eq(r.st, "R", k + " -> Needs review"); eq(r.note, "a profile exists but its source details are incomplete or invalid", k + ": explained");
    no(page, /<h5|Business Profile|<a href/, k + ": nothing from the profile is displayed"); re(cpage, /Business description[^]*?Needs review/, k + ": checklist says Needs review");
  });
  // escaping
  const evil = prof({ description: '<img src=x onerror=alert(1)> & "quoted"', key_businesses: ['"><script>alert(2)</script>', "<b>bold</b>"], business_model: "<i>model</i> & more", source_name: '<u>Name</u>"', source_date: "2026-09-17" });
  h = R.build("TCS", D(evil));
  no(h, /<img|<script|<b>bold|<i>model|<u>Name/, "no raw markup from the profile reaches the page");
  re(h, /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;quoted&quot;/, "description is escaped"); re(h, /&quot;&gt;&lt;script&gt;/, "key business escaped"); re(h, /&lt;i&gt;model&lt;\/i&gt; &amp; more/, "business model escaped");
  re(h, />&lt;u&gt;Name&lt;\/u&gt;&quot;<\/a>/, "source name escaped");
  no(C.build("TCS", D(evil)), /<img|<script/, "checklist output has no raw markup either");
  const hrefs = [...R.build("TCS", D(prof({ source_url: "https://www.tcs.com/a?x=1&y=2" }))).matchAll(/<a href="([^"]*)"/g)].map((m) => m[1]);
  eq(hrefs, ["https://www.tcs.com/a?x=1&amp;y=2"], "the only link is the https source link, with & escaped");
  eq([...R.build("TCS", D(prof({}))).matchAll(/<a [^>]*>/g)].every((m) => /^<a href="https:\/\/[^"]+" rel="noopener noreferrer">$/.test(m[0])), true, "every link is https with rel=noopener noreferrer");
  // the shipped profiles render for every listed stock
  list.forEach((p) => {
    const page = R.build(p.symbol, D(real)), cpage = C.build(p.symbol, D(real));
    re(page, /<h5[^>]*>Business Profile<\/h5>/, p.symbol + ": profile shown"); ok(text(page).includes(text(p.description.replace(/&/g, "&"))), p.symbol + ": description shown");
    ok(page.includes('href="' + new URL(p.source_url).href.replace(/&/g, "&amp;") + '"'), p.symbol + ": source link"); re(text(cpage), /Business description Profile on file Available/, p.symbol + ": checklist Available");
    re(page, p.source_date ? new RegExp("Source date: " + p.source_date) : /Source date: Not shown/, p.symbol + ": source date line");
    no(text(page + cpage), BANNED, p.symbol + ": no advice / rating wording on the rendered page");
  });
  // golden: nothing changes for a stock without a profile
  eq(sha(golden(html)), GOLDEN, "no-profile output is identical to the Phase 4 Step 2 output (one phrase normalised)");

  // ================= C. end-to-end placement with the fourth data file =================
  const fixtures = (withProf) => Object.assign({ "fundamentals.json": FUND, "financials.json": FIN, "historical.json": HIST }, withProf ? { "company_profiles.json": prof({}) } : {});
  async function boot(hash, files) {
    const fetched = [], observers = []; let boxHtml = "", children = [];
    const sched = () => queueMicrotask(() => observers.forEach((f) => f([])));
    const box = { id: "detail", get innerHTML() { return boxHtml + children.map((c) => c.innerHTML).join(""); }, set innerHTML(v) { boxHtml = v; children.forEach((c) => { c.parentNode = null; }); children = []; sched(); },
      appendChild(c) { c.parentNode = box; children.push(c); sched(); return c; }, setAttribute() {}, addEventListener() {}, querySelector: () => null };
    const els = {}, mk = (id) => ({ id, innerHTML: "", textContent: "", attrs: {}, setAttribute() {}, addEventListener() {}, querySelector: () => null });
    global.MutationObserver = function (cb) { this.observe = (el) => { if (el === box) observers.push(cb); }; };
    const lib = { createChart() { const s = () => ({ setData() {}, createPriceLine() {} }); return { addCandlestickSeries: s, addLineSeries: s, addHistogramSeries: s, timeScale: () => ({ fitContent() {}, subscribeVisibleLogicalRangeChange() {}, setVisibleLogicalRange() {} }), remove() {} }; } };
    global.window = { location: { hash }, addEventListener() {}, scrollTo() {}, LightweightCharts: lib };
    global.document = { body: { classList: { add() {}, remove() {}, contains: () => false } }, getElementById: (i) => (i === "detail" ? box : els[i] || (els[i] = mk(i))), addEventListener() {}, querySelector: () => null,
      createElement: () => ({ parentNode: null, innerHTML: "", id: "" }), head: { appendChild() {} } };
    global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()] ?? (u.endsWith("scans.json") ? { as_of: "2026-10-01", source: "NSE", stocks: [] } : null); return { ok: f !== null && f !== undefined, json: async () => JSON.parse(JSON.stringify(f)) }; };
    eval(detailCode); eval(chartCode); eval(techCode); eval(researchCode); eval(checkCode);
    await sleep(80);
    return { fetched, children: () => children, count: () => children.length, byId: (id) => children.find((c) => c.id === id) };
  }
  let t = await boot("#stock=TCS", fixtures(true));
  eq(t.count(), 2, "Stock Research and Investment Checklist are both placed, once each");
  re(t.byId("detailResearch").innerHTML, /Business Profile/, "end-to-end: Business Profile appears in Stock Research"); re(text(t.byId("detailChecklist").innerHTML), /Business description Profile on file Available/, "end-to-end: checklist row Available");
  eq(t.fetched.filter((u) => u.includes("company_profiles")).length, 2, "company_profiles.json is fetched once by Stock Research and once by the checklist (no other block)");
  ok(t.fetched.every((u) => /^out\/[a-z_]+\.json$/.test(u)), "all requests are relative out/*.json files"); ok(t.fetched.includes("out/company_profiles.json"), "the request path is out/company_profiles.json");
  t = await boot("#stock=TCS", fixtures(false));
  eq(t.count(), 2, "profile file missing (404): both sections still render"); no(t.byId("detailResearch").innerHTML, /Business Profile/, "no profile block"); eq(tile(t.byId("detailResearch").innerHTML, "Business description"), "Unavailable", "Unavailable when the file is missing");
  re(text(t.byId("detailChecklist").innerHTML), /Business description Unavailable Unavailable no verified business profile for this stock/, "checklist Unavailable with the reason");
  eq(tile(t.byId("detailResearch").innerHTML, "Company name"), "Tata Consultancy Services Ltd", "other Research data unaffected when the profile file is missing");
  const real2 = fixtures(false); real2["company_profiles.json"] = real;
  t = await boot("#stock=HDFCBANK", real2); no(t.byId("detailResearch").innerHTML, /Business Profile/, "end-to-end HDFCBANK: no profile"); eq(tile(t.byId("detailResearch").innerHTML, "Business description"), "Unavailable", "end-to-end HDFCBANK: Unavailable");
  t = await boot("#stock=ITC", real2); re(t.byId("detailResearch").innerHTML, /One of India's foremost private sector companies/, "end-to-end ITC: shipped profile shown");

  // ================= D. isolation =================
  eq(sha(blocks[0]), "3ee2ef6f101ccd3cd0df60bbd0bd37008c977e49c02e0cb3ba9caf8d128e1e5c", "dashboard block byte-identical");
  eq(sha(detailCode), "7db88e5847e49f38fb49a260ca6a6818e1610f4c3f09e783702fd86233e7ad10", "Stock Detail block byte-identical");
  eq(sha(chartCode), "ef5a348496d6aafa87c6352665fd3475d56d0bd778e73c062af759ec7b9dfc53", "chart + MACD block byte-identical");
  eq(sha(techCode), "5f187e38d73cb15eed203fbc0cc41deb7a88dce219c6eee6f55b6f99e719b339", "Technical Snapshot block byte-identical");
  eq(sha(blocks[blocks.length - 1]), "35de0d215ec3947da870f95e636f41bf4b130d6d929be4d91d3deb2661344d2f", "final Phase 2B block byte-identical");
  eq(blocks.length, 7, "still seven script blocks");
  eq((researchCode.match(/fetch\(/g) || []).length, 1, "research: still one fetch helper"); eq((checkCode.match(/fetch\(/g) || []).length, 1, "checklist: still one fetch helper");
  eq([...new Set(researchCode.match(/out\/[a-z_]+\.json/g))].sort(), ["out/company_profiles.json", "out/financials.json", "out/fundamentals.json", "out/historical.json"], "research reads only the four static files");
  no(html, /UPSTOX_ANALYTICS_TOKEN|Bearer\s|Authorization/i, "no token or auth header in the page"); no(html, /api\.upstox\.com/, "no Upstox call in the page");
  no(researchCode + checkCode, /\b(buy|sell|bullish|bearish|undervalued|overvalued|recommend\w*)\b/i, "no advice wording in the two changed blocks (the existing Step 1 / Step 2 suites check the rest)");
  ok(!fs.existsSync(path.join(ROOT, "out")) || JSON.stringify(fs.readdirSync(path.join(ROOT, "out"))) === JSON.stringify(["growth_events.json"]), "no generated out/ folder is shipped (the only file under out/ is the curated growth_events.json of Phase 5G.2)");

  console.log("Profile tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
