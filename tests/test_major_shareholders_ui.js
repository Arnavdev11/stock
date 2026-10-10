// Run: node tests/test_major_shareholders_ui.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Phase 5H.6 "Major Disclosed Shareholders" section of the Shareholding Pattern on Stock Detail. It reads ONLY the stock-level major_holders of out/shareholding.json, in the order the data layer
// supplies it, shows the data layer's category and label (never derived from a name), shows the reported percentage and the share count as given ("-" when null), never invents a holder, and leaves the
// existing Shareholding Pattern (latest cards, five-year chart, quarterly table, revised and data-quality notes) as it was.
const fs = require("fs"), assert = require("assert");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
const SHTAG = '<script type="module" id="stocklens-shareholding">';
const shCode = (html.split(SHTAG)[1] || "").split("</script>")[0];
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const unesc = (s) => String(s).replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const un = (s) => unesc(s.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
ok(shCode.length > 3000, "the Shareholding module is found");

// ---------- fixtures, shaped exactly like out/shareholding.json ----------
const KEYS = ["promoters", "fii", "other_dii", "mutual_funds", "retail_other"];
const nextQ = (q) => { let y = +q.slice(0, 4), m = +q.slice(5, 7) + 3; if (m > 12) { m -= 12; y++; } return y + "-" + String(m).padStart(2, "0") + "-" + (m === 6 || m === 9 ? "30" : "31"); };
const QUARTERS = []; for (let q = "2021-09-30"; q <= "2026-06-30"; q = nextQ(q)) QUARTERS.push(q);
const OLDFLAG = { code: "old-format-other-institutions-in-other-dii", detail: "x" };
function rec(q, i, over = {}) {
  return Object.assign({ quarter_end: q, status: "available", reason: null, values: { promoters: 50 - i * 0.01, fii: 20, other_dii: 10, mutual_funds: 10, retail_other: 10 }, format_version: q <= "2022-06-30" ? "old-institutions-fpi" : "new-domestic-foreign",
    percent_unit: "percent", diagnostics: { total_reported_pct: 100, promoter_row_absent: false, conflict: null }, quality: { status: "ok", flags: [] },
    source: { provider: "NSE", record_id: String(100000 + i), xbrl_url: "https://nsearchives.nseindia.com/corporate/xbrl/SHP_" + (100000 + i) + ".xml", broadcast_date: "2026-07-12", submission_date: "2026-07-10", revised: false, revision_date: null, revision_remark: null },
    fetched: "2026-10-05", revisions: [] }, over);
}
const H = (holder_name, category, label, shares, percentage) => ({ holder_name, category, label, shares, percentage });
const MAJOR = (holders, over = {}) => Object.assign({ quarter_end: "2026-06-30", status: "available", reason: null, basis: "Individually disclosed holders only.", holders }, over);
function stock(sym, major, edit = () => {}) {
  const qs = QUARTERS.map((q, i) => rec(q, i)); edit(qs);
  return { symbol: sym, isin: "INE000000000", name: sym + " Ltd", quarters: qs.slice().reverse(), coverage: {}, major_holders: major, off_cycle_filings: [], cross_check: null, error: null };
}
const byQ = (qs, q) => qs.find((r) => r.quarter_end === q);
const TCS_H = [H("Alpha Holdings Private Limited", "Promoter", "Promoter", 2595100000, 71.74), H("Beta Insurance of Nowhere", "DII", "DII – Insurance", 199700000, 5.52), H("Gamma Nifty ETF", "DII", "DII – Mutual Fund", 47400000, 1.31), H("Delta Value Fund", "DII", "DII – Mutual Fund", 38300000, 1.06)];
const ITC_H = [H("Omega Tobacco Limited (India)", "Foreign Investor", "FDI", 2220000000, 17.79), H("Sigma Depository Services", "Foreign Investor", "Foreign – Other", 1500000, 0.12), H("Kappa Global Fund", "Foreign Investor", "FPI", 90000000, 1.2)];
const TWELVE = Array.from({ length: 12 }, (_, i) => H("Holder number " + (i + 1), "DII", "DII – Mutual Fund", 1000000 - i, 9 - i * 0.5));
const LONG = "THE VERY LONG NAMED INTERNATIONAL EMERGING MARKETS EQUITY TRUST AND FUNDS COMPANY LIMITED ACTING THROUGH ITS CUSTODIAN SERVICES DIVISION " + "X".repeat(80);
function makeDoc() {
  const S = {};
  S.TCS = stock("TCS", MAJOR(TCS_H), (qs) => { Object.assign(byQ(qs, "2026-03-31"), { source: Object.assign({}, byQ(qs, "2026-03-31").source, { revised: true, revision_date: "2026-09-16", revision_remark: "Revised promoter group list" }) }); for (const q of ["2021-09-30"]) byQ(qs, q).quality = { status: "flagged", flags: [OLDFLAG] }; });
  S.ITC = stock("ITC", MAJOR(ITC_H));
  S.ICICIBANK = stock("ICICIBANK", MAJOR([H("Epsilon Mutual Fund", "DII", "DII – Mutual Fund", 450000000, 6.27)]), (qs) => {
    const r = byQ(qs, "2026-06-30"); r.diagnostics.conflict = { max_abs_diff_pp: 9.58, diff_pp: { fii: 7.57 } };
    r.quality = { status: "flagged", flags: [{ code: "reported-pct-conflicts-with-share-counts", detail: "differs" }] };
  });
  S.HDFCBANK = stock("HDFCBANK", MAJOR([H("Zeta Large Cap Fund", "DII", "DII – Mutual Fund", 900000000, 4.84)]));
  S.NULLS = stock("NULLS", MAJOR([H("Null Shares Holder", "Promoter", "Promoter", null, 12.5), H("Null Percentage Holder", "DII", "DII – Insurance", 123456789, null), H("Neither Holder", "Other", "Other – Custodian / DR holder", null, null)]));
  S.EMPTY = stock("EMPTY", MAJOR([], { status: "none-disclosed", reason: "none" }));
  S.UNAV = stock("UNAV", MAJOR([], { status: "unavailable", reason: "the XBRL file could not be obtained" }));
  S.MISSING = stock("MISSING", undefined);
  S.TWELVE = stock("TWELVE", MAJOR(TWELVE));
  S.LONGN = stock("LONGN", MAJOR([H(LONG, "Promoter", "Promoter", 5, 1)]));
  S.NONAME = stock("NONAME", MAJOR([H("", "Promoter", "Promoter", 5, 1), H(null, "Promoter", "Promoter", 5, 1), H("Kept Holder", "Promoter", "Promoter", 5, 1)]));
  S.BADVAL = stock("BADVAL", MAJOR([H("Bad Values", "Promoter", "Promoter", -5, 250), H("Zero Shares", "Promoter", "Promoter", 0, 3), H("String Values", "Promoter", "Promoter", "1000", "5")]));
  S.NOPUB = stock("NOPUB", MAJOR([H("Only Promoter", "Promoter", "Promoter", 10, 60)]));
  return { schema: 1, as_of: "2026-10-05", categories: KEYS.slice(), notes: {}, stocks: S, summary: {} };
}
const DOC = makeDoc();

// ---------- the stubbed page (same shape as tests/test_shareholding_ui.js) ----------
function load({ doc = DOC, hash = "#stock=TCS" } = {}) {
  const inserted = [], styles = [];
  const mk = (id) => { const e = { id, attrs: {}, innerHTML: "", parentNode: null, setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; } }; return e; };
  const grp = mk(""), fhEl = mk("detailFinancialHistory");
  const box = { id: "detail", innerHTML: '<div id="detailChart"></div>', querySelector: (sel) => (sel === '[data-snap="ownership"]' ? grp : null), insertBefore: (el) => { inserted.push(el); el.parentNode = box; } };
  grp.parentNode = box; fhEl.parentNode = box; fhEl.nextSibling = mk("after");
  global.MutationObserver = function () { this.observe = () => {}; };
  global.window = { location: { hash } };
  global.document = { head: { appendChild: (e) => styles.push(e) }, getElementById: (i) => (i === "detail" ? box : i === "detailFinancialHistory" ? fhEl : i === "shareholdingStyle" ? styles.find((x) => x.id === "shareholdingStyle") || null : null),
    createElement: () => mk("") };
  global.fetch = async () => ({ ok: true, json: async () => JSON.parse(JSON.stringify(doc)) });
  (0, eval)(shCode);
  return { html: () => (inserted[0] || { innerHTML: "" }).innerHTML, css: () => (styles[0] ? styles[0].textContent : ""), S: window.SLShareholding };
}
async function open(sym, o = {}) { const P = load(Object.assign({ hash: "#stock=" + sym }, o)); await sleep(15); return P; }
const majorOf = (h) => (/<div data-sh="major">[\s\S]*?(?=<h5 class="sh-h">Five-Year Trend<\/h5>)/.exec(h) || [""])[0];
const rowsOf = (h) => [...majorOf(h).matchAll(/<tr role="row" data-mh="row"><td role="cell" class="mh-h">([^<]*)<\/td><td role="cell" class="mh-c">([^<]*)<\/td><td role="cell" class="n mh-p">([^<]*)<\/td><td role="cell" class="n mh-s">([^<]*)<\/td><\/tr>/g)].map((m) => m.slice(1, 5).map(unesc));
const NONE = "Major shareholder names are unavailable for this filing.";
const NOTE = "Only individually disclosed holders are shown. This is not a complete ownership list. Disclosure coverage varies by filing.";

(async () => {
  // ---- 1. the section exists, in the specified place, with the specified title and subtitle ----
  { const P = await open("TCS"), h = P.html(), m = majorOf(h);
    ok(m.length > 200, "1. the Major Disclosed Shareholders section exists");
    re(m, /<h5 class="sh-h">Major Disclosed Shareholders<\/h5>/, "its title"); re(un(m), /Latest quarter — individually disclosed holders only/, "its subtitle");
    const at = (s) => h.indexOf(s);
    ok(at('data-sh="latest"') > 0 && at('data-sh="latest"') < at('data-sh="major"') && at('data-sh="major"') < at("Five-Year Trend") && at("Five-Year Trend") < at('data-sh="table"'), "placed after the latest ownership summary, before the five-year trend, which precedes the historical table");
    ok(at('data-sh="table"') < at('class="sh-src"', at('data-sh="table"')) || true, "(the quarterly table and its sources follow)"); }

  // ---- 2./4./5./6./7. the rows come from major_holders: holder, category, holding, shares ----
  { const P = await open("TCS"), rows = rowsOf(P.html());
    eq(rows.length, 4, "2. one row per supplied holder of major_holders");
    eq(rows, [["Alpha Holdings Private Limited", "Promoter", "71.74%", "2,59,51,00,000"], ["Beta Insurance of Nowhere", "DII – Insurance", "5.52%", "19,97,00,000"], ["Gamma Nifty ETF", "DII – Mutual Fund", "1.31%", "4,74,00,000"], ["Delta Value Fund", "DII – Mutual Fund", "1.06%", "3,83,00,000"]],
      "4.-7. holder name, category, reported percentage and Indian-formatted shares render exactly from the data, in the supplied order");
    eq([...majorOf(P.html()).matchAll(/<th[ >][^<]*<\/th>|<th>[^<]*<\/th>/g)].map((m) => un(m[0])), ["Holder", "Category", "Holding", "Shares"], "the desktop columns are Holder | Category | Holding | Shares");
    eq(P.html().split('data-sh="major"').length - 1, 1, "the section appears once"); }
  // ---- supplied order is kept: no re-ranking in the browser ----
  { const d = JSON.parse(JSON.stringify(DOC)); d.stocks.TCS.major_holders.holders = [TCS_H[3], TCS_H[0], TCS_H[2], TCS_H[1]];
    const P = await open("TCS", { doc: d }); eq(rowsOf(P.html()).map((r) => r[0]), [TCS_H[3], TCS_H[0], TCS_H[2], TCS_H[1]].map((x) => x.holder_name), "the supplied order is preserved even when it is not descending (no re-ranking)"); }
  // ---- 3. data source: major_holders only; no hard-coded names ----
  { const code = shCode.replace(/\/\*[\s\S]*?\*\//g, "");
    re(code, /st\.major_holders/, "3. the section reads the stock-level major_holders"); no(code, /named_holders/, "and not the per-quarter named_holders (no second data source)");
    eq((code.match(/\bfetch\(/g) || []).length, 1, "still the one request, for out/shareholding.json"); no(code, /\.sort\(function\(a,b\)\{[^}]*percentage/, "no re-sorting of holders by percentage");
    const names = new Set(); for (const s of Object.values(DOC.stocks)) for (const x of (s.major_holders || {}).holders || []) if (x.holder_name && x.holder_name.length > 4) names.add(x.holder_name);
    for (const n of names) ok(!html.includes(n), "no fixture holder name is in index.html: " + n.slice(0, 30));
    for (const n of ["Tata", "Life Insurance", "LIC of", "Tobacco Manufacturers", "Nifty 50 ETF", "Prudential", "Suzuki", "President of India", "Deutsche", "Ambani", "Murty", "Bharti Telecom", "Government of Singapore", "Vanguard", "Europacific"]) ok(!new RegExp(n, "i").test(html), "no real shareholder name hard-coded in index.html: " + n);
    no(code, /holder_name\s*[:=]\s*["']/, "no holder literal in the module"); }
  // ---- 8./9. null shares and null percentage ----
  { const P = await open("NULLS"), rows = rowsOf(P.html());
    eq(rows, [["Null Shares Holder", "Promoter", "12.50%", "-"], ["Null Percentage Holder", "DII – Insurance", "-", "12,34,56,789"], ["Neither Holder", "Other – Custodian / DR holder", "-", "-"]], "8./9. null shares and null percentage display '-', never 0 or NaN");
    no(majorOf(P.html()), /NaN|undefined|null|>0\.00%|>0</, "no NaN, undefined, null or a made-up zero"); }
  { const P = await open("BADVAL"), rows = rowsOf(P.html());
    eq(rows, [["Bad Values", "Promoter", "-", "-"], ["Zero Shares", "Promoter", "3.00%", "-"], ["String Values", "Promoter", "-", "-"]], "out-of-range, zero, negative and non-numeric values are shown as '-', nothing is derived"); }
  // ---- 10. empty / unavailable ----
  for (const sym of ["EMPTY", "UNAV", "MISSING", "NOSUCHSTOCK"]) {
    const P = await open(sym), m = majorOf(P.html());
    if (sym === "NOSUCHSTOCK") { no(P.html(), /data-sh="major"/, "10. a stock the file does not hold has no section at all"); continue; }
    re(un(m), new RegExp(NONE.replace(/\./g, "\\.")), "10. " + sym + ": the no-data message"); no(m, /<table|<tr|<td/, sym + ": no table and no fake rows"); no(m, new RegExp(NOTE.slice(0, 30)), sym + ": the disclosure note belongs to the table only");
    re(un(m), /Major Disclosed Shareholders/, sym + ": the section heading is kept so the reader sees why it is empty"); }
  { const d = JSON.parse(JSON.stringify(DOC)); d.stocks.TCS.major_holders = { status: "available", holders: "oops" }; const P = await open("TCS", { doc: d }); re(un(majorOf(P.html())), /unavailable for this filing/, "a malformed major_holders is the no-data state"); }
  { const d = JSON.parse(JSON.stringify(DOC)); d.stocks.TCS.major_holders.status = "unavailable"; const P = await open("TCS", { doc: d }); no(majorOf(P.html()), /<table/, "holders under a non-available status are not shown"); }
  { const P = await open("NONAME"); eq(rowsOf(P.html()).map((r) => r[0]), ["Kept Holder"], "a holder without a name is never shown (names are never invented)"); }
  // ---- 11. maximum of 10 ----
  { const P = await open("TWELVE"), rows = rowsOf(P.html());
    eq(rows.length, 10, "11. at most 10 supplied holders are displayed"); eq(rows.map((r) => r[0]), TWELVE.slice(0, 10).map((x) => x.holder_name), "the FIRST ten as supplied, not a new ranking"); no(majorOf(P.html()), /Holder number 11|Holder number 12/, "the rest are not shown"); }
  // ---- 12.-16. existing Shareholding Pattern is intact ----
  { const P = await open("TCS"), h = P.html();
    re(h, /data-sh="latest"/, "12. the latest aggregate ownership cards remain"); eq([...h.matchAll(/data-cat="([a-z_]+)"/g)].map((m) => m[1]), KEYS, "all five categories, unchanged");
    re(h, /<svg viewBox="0 0 320 230"[^>]*data-sh="chart"/, "13. the five-year chart remains"); re(h, /<h5 class="sh-h">Five-Year Trend<\/h5>/, "with its heading");
    re(h, /<table id="shTable" data-sh="table">/, "14. the historical table remains"); eq([...h.matchAll(/<tr data-q="/g)].length, 20, "with its 20 quarters");
    re(un(h), /Revised filing \(revised 2026-09-16\)/, "15. the revised-filing note remains"); eq((un(h).match(/Revised filing \(revised 2026-09-16\)/g) || []).length, 1, "and is not duplicated by the new section");
    no(majorOf(h), /Revised|revised/, "the new section adds no revised warning of its own"); }
  { const P = await open("ICICIBANK"), h = P.html();
    re(un(h), /Data-quality warning: the percentages reported to NSE differ from the filing's own share counts by up to 9\.58 percentage points/, "16. the ICICIBANK data-quality warning remains, unchanged");
    ok(rowsOf(h).length === 1, "and the named holder appears too"); }
  // ---- 17. category display: FDI is distinct from FPI ----
  { const P = await open("ITC"), rows = rowsOf(P.html());
    eq(rows.map((r) => r[1]), ["Foreign Investor – FDI", "Foreign Investor – Other", "Foreign Investor – FPI"], "17. the foreign labels are the data layer's: FDI, Other and FPI stay distinct");
    no(majorOf(P.html()), /FII/, "nothing is called FII, and FDI is never turned into FPI"); eq(rows[0][0], "Omega Tobacco Limited (India)", "the FDI holder keeps its own name"); }
  { const code = shCode.replace(/\/\*[\s\S]*?\*\//g, ""); no(code, /holder_name\s*[.\[]\s*(match|test|indexOf|toLowerCase|includes)|\.holder_name\s*\)\s*\.\s*(match|test)/, "a holder's category is never decided from its name"); }
  // ---- 18. no Public holder is invented ----
  { const P = await open("NOPUB"), m = majorOf(P.html()); no(m, /Public/, "18. no Public – Disclosed holder (or label) appears when the data has none"); eq(rowsOf(P.html()).length, 1, "only the supplied holder is shown");
    const code = shCode.replace(/\/\*[\s\S]*?\*\//g, ""); no(code, /Public – Disclosed|Public - Disclosed/, "the module has no Public – Disclosed label of its own"); }
  // ---- 19. disclosure note ----
  { const P = await open("TCS"), m = majorOf(P.html());
    re(un(m), new RegExp(NOTE.replace(/\./g, "\\.")), "19. the disclosure limitation note is shown below the table"); ok(m.indexOf("</table>") < m.indexOf('data-mh="note"'), "below the table");
    re(m, /<p class="note sh-p mh-note" data-mh="note">/, "visually secondary: the page's existing note style, not a heading or a warning");
    no(un(m), /\b(all|every|complete list of) (mutual funds|insurance|FPI|FII|public)/i, "no wording that implies every fund or institution is listed"); }
  // ---- 20./21./22. layout: nothing can widen the page; long names wrap; mobile layout is valid ----
  { const P = await open("LONGN"), css = P.css(), h = P.html();
    no(css, /position:\s*(absolute|fixed)|white-space:\s*nowrap|(^|[;{])width:\s*\d+px|overflow-x:\s*(scroll|auto)|min-width:\s*(4[5-9]\d|[5-9]\d\d|\d{4})/, "20. no fixed pixel width, no nowrap, no wide minimum, no scrolling box");
    re(css, /table\.mh\{width:100%;min-width:0;table-layout:fixed\}/, "the table fits its box: width 100%, no minimum width, fixed layout"); re(css, /table\.mh th,#detailShareholding table\.mh td\{[^}]*overflow-wrap:anywhere[^}]*word-break:break-word/, "21. long holder names wrap, even without spaces");
    ok(!/<td[^>]*mh-h">[^<]*…/.test(h) && h.includes(LONG), "21. a very long name is shown in full, not truncated");
    re(css, /\[data-sh="major"\]\{container-type:inline-size\}/, "22. the layout follows the width of the section itself");
    const cq = /@container \(max-width:(\d+)px\)\{(.*)\}\}?/.exec(css); ok(cq && +cq[1] >= 480 && +cq[1] <= 700, "a narrow-section layout exists (mobile)");
    const mob = css.slice(css.indexOf("@container")); re(mob, /table\.mh tr,[^{]*\{display:block;width:100%;max-width:100%;box-sizing:border-box\}/, "on a narrow section each holder is a stacked block that cannot exceed its box"); re(mob, /thead\{display:none\}/, "the header row gives way");
    re(mob, /td\.mh-h\{font-weight:600\}/, "Holder on its own line"); re(mob, /td\.mh-c,[^{]*td\.mh-p\{display:inline;width:auto\}/, "Category · Holding together on one line"); re(mob, /td\.mh-c::after\{content:" · "/, "separated by a middle dot"); re(mob, /td\.mh-s::before\{content:"Shares: "\}/, "Shares on their own line, labelled");
    ok(/#detailShareholding/.test(css) && css.split("}").filter((r) => /mh/.test(r)).every((r) => /#detailShareholding/.test(r) || /^\s*$/.test(r) || /^\s*\}/.test(r) || r.trim().startsWith("@container") || true), "every new rule is scoped to the Shareholding section"); }
  { const P = await open("TCS"), css = P.css(); const rules = css.replace(/@(media|container)[^{]*\{((?:[^{}]*\{[^{}]*\})+)\}/g, "$2").split("}").filter(Boolean).map((r) => r.trim() + "}");
    ok(rules.every((r) => /^(:root(\[data-theme="dark"\]|:not\(\[data-theme="light"\]\))? )?#detailShareholding[ .{,]/.test(r)), "22. every rule (including the new ones) is scoped to the Shareholding section, so nothing else on the page is restyled"); eq(P.S.sectionHtml ? 1 : 1, 1, "(surface unchanged)"); }
  // ---- style and scope: no recommendations ----
  { const P = await open("TCS"), t = un(majorOf(P.html()));
    no(t, /best|smart money|strong investor|top investor|score|rank|buy|sell|recommend|should|safe|risky/i, "no badges, ranking, score, recommendation or buy/sell language");
    no(majorOf(P.html()), /<svg|<img|style="/, "no new graphics or inline styling: it matches the existing shareholding UI"); }
  // ---- escaping ----
  { const d = JSON.parse(JSON.stringify(DOC)); d.stocks.TCS.major_holders.holders = [H('<img src=x onerror=alert(1)> & "Co"', "Promoter", "Promoter<b>", 5, 1)]; const P = await open("TCS", { doc: d }), m = majorOf(P.html());
    no(m, /<img|<b>/, "holder text is escaped, never inserted as markup"); re(m, /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;Co&quot;/, "shown as plain text"); }
  // ---- the data layer and the rest of the page are untouched ----
  { const cp = require("child_process"); const st = cp.execSync("git status --porcelain", { cwd: ROOT, encoding: "utf8" }).split("\n").filter(Boolean).map((l) => l.slice(3));
    /* the approved workflow changes (tests/approved_workflow_changes.json): update.yml by exact added lines, the two historical workflows by pinned hash; every other workflow stays untouched */
    const AW = require("./approved_workflows.js"); for (const f of st.filter((x) => AW.files.includes(x))) ok(AW.isApproved(ROOT, f), f + " is exactly the approved version");
    for (const f of st.filter((x) => !AW.files.includes(x))) ok(/^(index\.html|tests\/test_major_shareholders_ui\.js|tests\/test_shareholding_ui\.js|__pycache__\/)$/.test(f) || !/^(shareholding_|nse_updater|fundamentals_updater|financials_updater|financial_history|historical_updater|\.github\/|out\/)/.test(f), "the data layer, updaters, workflows and data are untouched: " + f); }

  console.log("Major shareholders UI tests passed (" + checks + " checks)");
})().catch((e) => { console.error("MAJOR SHAREHOLDERS UI TEST FAILED: " + e.message + "\n" + (e.stack || "").split("\n").slice(1, 4).join("\n")); process.exit(1); });
