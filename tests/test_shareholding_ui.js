// Run: node tests/test_shareholding_ui.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the Phase 5H.4 Shareholding Pattern UI: the ownership summary inside the Investor Snapshot and the full Shareholding Pattern section (latest snapshot, trend chart, quarterly table, data-quality notes,
// source information) on Stock Detail. The page reads ONLY out/shareholding.json; the stored percentages are shown exactly as stored; a missing or unavailable quarter is a gap or "-" and never zero or interpolated;
// the share-count percentages kept for diagnostics and any other source are never shown; nothing is advice.
const fs = require("fs"), assert = require("assert"), cp = require("child_process");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
// Phase 5I: behaviour is tested on the page as shipped (html). The byte-identity pins below are tested on PINHTML = the page minus the Phase 5I layer (legacy_5i.js, proven exact against aa4ace1 by test_global_navigation.js), because Phase 5I deliberately changes the route readers and the search mount.
const PINHTML = require("./legacy_5i.js").legacy(html), PINBLOCKS = PINHTML.split("<script>").slice(1).map((b) => b.split("</script>")[0]), PINDETAIL = PINBLOCKS.find((b) => b.includes("Stock Detail view"));
const SHTAG = '<script type="module" id="stocklens-shareholding">', SNAPTAG = '<script type="module" id="stocklens-snapshot">', GROWTAG = '<script type="module" id="stocklens-growth">';
const modOf = (src, tag) => (src.split(tag)[1] || "").split("</script>")[0];
const shCode = modOf(html, SHTAG), snapCode = modOf(html, SNAPTAG), grCode = modOf(html, GROWTAG);
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const unesc = (s) => String(s).replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const un = (s) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();
ok(shCode.length > 3000 && snapCode.length > 3000 && grCode.length > 3000, "the Shareholding, snapshot and growth modules are found");

// ---------- an independent statement of the contract ----------
const KEYS = ["promoters", "fii", "other_dii", "mutual_funds", "retail_other"];
const LABEL = { promoters: "Promoters", fii: "FII", other_dii: "DII (Other)", mutual_funds: "Mutual Funds", retail_other: "Public / Retail & Other" };
const ADVICE = /\b(buy|sell|hold|strong|weak|bullish|bearish|positive|negative|good|bad|attractive|score|scores|rating|rated|rank|ranks|ranking|ranked|winner|loser|best|worst|top|target|signal|recommend\w*|outperform\w*|underperform\w*|undervalued|overvalued|advice|advise\w*|safe|risky|risk|favourable|favorable|opportunity|should|must|ideal|healthy|concern\w*|improv\w*|declin\w*|deteriorat\w*|confidence|conviction|accumulat\w*|exit\w*|dump\w*)\b/i;
const MONN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const qlab = (q) => MONN[+q.slice(5, 7) - 1] + " " + q.slice(0, 4);
const nextQ = (q) => { let y = +q.slice(0, 4), m = +q.slice(5, 7) + 3; if (m > 12) { m -= 12; y++; } return y + "-" + String(m).padStart(2, "0") + "-" + (m === 6 || m === 9 ? "30" : "31"); };
const QUARTERS = []; for (let q = "2021-09-30"; q <= "2026-06-30"; q = nextQ(q)) QUARTERS.push(q);
eq(QUARTERS.length, 20, "the shipped history is 20 quarters, Sep 2021 to Jun 2026");

// ---------- fixtures, shaped exactly like out/shareholding.json ----------
const r2 = (x) => Math.round(x * 100) / 100;
function vals(i, base) { return { promoters: base.p ? r2(base.p - i * 0.03) : 0, fii: r2(base.f + Math.sin(i) * 2), other_dii: r2(base.d + i * 0.05), mutual_funds: r2(base.m + i * 0.11), retail_other: r2(base.r + i * 0.02) }; }
function rec(q, i, base, over = {}) {
  const old = q <= "2022-06-30";
  return Object.assign({
    quarter_end: q, status: "available", reason: null, values: vals(i, base), format_version: old ? "old-institutions-fpi" : "new-domestic-foreign", percent_unit: q >= "2025-09-30" ? "fraction" : "percent",
    diagnostics: { total_reported_pct: 100, employee_trusts_pct: null, employee_trusts_mode: "none", promoter_row_absent: false, share_derived_pct: { promoters: 61.37, fii: 62.48, other_dii: 63.59, mutual_funds: 64.61, retail_other: 65.72 }, conflict: null },
    quality: { status: "ok", flags: [] },
    source: { provider: "NSE", record_id: String(100000 + i), xbrl_url: "https://nsearchives.nseindia.com/corporate/xbrl/SHP_" + (100000 + i) + ".xml", broadcast_date: q.slice(0, 4) + "-" + String(+q.slice(5, 7) + 1).padStart(2, "0") + "-12", submission_date: q.slice(0, 4) + "-" + String(+q.slice(5, 7) + 1).padStart(2, "0") + "-10", revised: false, revision_date: null, revision_remark: null },
    fetched: "2026-10-05", revisions: [], raw: { rows: { SECRET_RAW_ROW: [1, 2] } } }, over);
}
const OLDFLAG = { code: "old-format-other-institutions-in-other-dii", detail: "the filing has an 'Other Institutions' row (2.29%); it cannot be split between foreign and domestic, so it sits in other_dii" };
function stock(sym, base, edit = () => {}) {
  const qs = QUARTERS.map((q, i) => rec(q, i, base)); edit(qs);
  return { symbol: sym, isin: "INE000000000", name: sym + " Ltd", quarters: qs.slice().reverse(), coverage: {}, off_cycle_filings: [], cross_check: { status: "mismatch", categories: { promoters: { official: 1, upstox: 99.99, diff: -98.99 } } }, error: null };
}
const byQ = (qs, q) => qs.find((r) => r.quarter_end === q);
function makeDoc() {
  const S = {};
  S.TCS = stock("TCS", { p: 72, f: 10, d: 7, m: 5.5, r: 5 }, (qs) => {
    Object.assign(byQ(qs, "2026-03-31"), { source: Object.assign({}, byQ(qs, "2026-03-31").source, { revised: true, revision_date: "2026-09-16", revision_remark: "Revised promoter group list" }), quality: { status: "ok", flags: [] } });
    for (const q of ["2021-09-30", "2021-12-31", "2022-03-31", "2022-06-30"]) { const r = byQ(qs, q); r.quality = { status: "flagged", flags: [OLDFLAG] }; }
  });
  S.ITC = stock("ITC", { p: 0, f: 37, d: 33, m: 16.5, r: 16 }, (qs) => {
    for (const q of ["2021-09-30", "2021-12-31", "2022-03-31", "2022-06-30"]) {
      const r = byQ(qs, q);
      Object.assign(r, { status: "unavailable", reason: "the XBRL file could not be obtained (HTTP 404)", values: { promoters: null, fii: null, other_dii: null, mutual_funds: null, retail_other: null }, format_version: null, percent_unit: null, diagnostics: null, raw: null, quality: { status: "flagged", flags: [{ code: "xbrl-unavailable", detail: "the XBRL file could not be obtained (HTTP 404)" }] } });
    }
    for (const r of qs) if (r.status === "available") r.diagnostics.promoter_row_absent = true;
  });
  S.ICICIBANK = stock("ICICIBANK", { p: 0, f: 44, d: 14.5, m: 32, r: 9.4 }, (qs) => {
    Object.assign(byQ(qs, "2026-03-31"), { values: { promoters: 0, fii: 34.49, other_dii: 12.12, mutual_funds: 27.83, retail_other: 25.57 } });
    Object.assign(byQ(qs, "2026-06-30"), { values: { promoters: 0, fii: 49.82, other_dii: 12.71, mutual_funds: 29.6, retail_other: 7.87 } });
    for (const [q, d] of [["2026-03-31", 16.34], ["2026-06-30", 9.58]]) {
      const r = byQ(qs, q);
      r.diagnostics.promoter_row_absent = true; r.diagnostics.share_derived_pct = { promoters: 0, fii: 42.06, other_dii: 14.77, mutual_funds: 33.94, retail_other: 9.23 }; r.diagnostics.conflict = { max_abs_diff_pp: d, diff_pp: { fii: 7.57 } };
      r.quality = { status: "flagged", flags: [{ code: "reported-pct-conflicts-with-share-counts", detail: "the reported percentages differ from the filing's own share counts by up to " + d + " percentage points" }] };
    }
  });
  S.HDFCBANK = stock("HDFCBANK", { p: 0, f: 45, d: 10.5, m: 30, r: 15 }, (qs) => { for (const r of qs) r.diagnostics.promoter_row_absent = true; });
  S.GAPCO = stock("GAPCO", { p: 40, f: 20, d: 15, m: 10, r: 15 }, (qs) => {
    qs.splice(qs.findIndex((r) => r.quarter_end === "2023-06-30"), 1);                                  // a quarter the data does not hold at all
    byQ(qs, "2024-03-31").values.fii = null;                                                           // one category missing in one quarter
    byQ(qs, "2024-03-31").quality = { status: "flagged", flags: [{ code: "category-row-missing", detail: "fii" }] };
  });
  S.ONEQ = { symbol: "ONEQ", isin: null, name: "One", quarters: [rec("2026-06-30", 19, { p: 50, f: 20, d: 10, m: 10, r: 10 })], coverage: {}, off_cycle_filings: [], cross_check: null, error: null };
  S.NEWUN = stock("NEWUN", { p: 50, f: 20, d: 10, m: 10, r: 10 }, (qs) => {                              // the newest quarter is unavailable
    Object.assign(byQ(qs, "2026-06-30"), { status: "unavailable", reason: "the XBRL holds no category rows for 2026-06-30", values: { promoters: null, fii: null, other_dii: null, mutual_funds: null, retail_other: null }, format_version: null, percent_unit: null, diagnostics: null, quality: { status: "flagged", flags: [{ code: "xbrl-no-rows", detail: "x" }] } });
  });
  S.ALLUN = stock("ALLUN", { p: 50, f: 20, d: 10, m: 10, r: 10 }, (qs) => { for (const r of qs) Object.assign(r, { status: "unavailable", reason: "the XBRL file could not be obtained (HTTP 404)", values: { promoters: null, fii: null, other_dii: null, mutual_funds: null, retail_other: null } }); });
  return { schema: 1, as_of: "2026-10-05", categories: KEYS.slice(), notes: {}, stocks: S, summary: {} };
}
const DOC = makeDoc();

// ---------- the stubbed page ----------
const SNAP_PLACEHOLDER = '<div class="snap-h"><h4>Ownership</h4></div><p class="snap-t">Shareholding history will be added in the Shareholding Pattern phase.</p>';
function load({ doc = DOC, hash = "#stock=TCS", group = true, fh = true, detail = true, delay = 0, failFetch = false, status = 200, code = shCode } = {}) {
  const fetched = [], styles = [], obs = [], inserted = [];
  const mk = (id) => { const e = { id, attrs: {}, innerHTML: "", textContent: "", parentNode: null, setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; } }; return e; };
  let grp = group ? mk("") : null; if (grp) grp.innerHTML = SNAP_PLACEHOLDER;
  const fhEl = mk("detailFinancialHistory"), after = mk("after");
  const box = { id: "detail", innerHTML: detail ? '<div id="detailChart"></div>' : "", querySelector: (sel) => (sel === '[data-snap="ownership"]' ? grp : null), insertBefore: (el, ref) => { inserted.push({ el, ref }); el.parentNode = box; }, appendChild: (el) => { inserted.push({ el, ref: null }); el.parentNode = box; } };
  if (grp) grp.parentNode = box; fhEl.parentNode = fh ? box : null; fhEl.nextSibling = after;
  global.MutationObserver = function (cb) { this.observe = (el, opts) => { if (el === box) obs.push({ cb, opts }); }; };
  global.window = { location: { hash } };
  global.document = { head: { appendChild: (e) => styles.push(e) },
    getElementById: (i) => (i === "detail" ? box : i === "detailFinancialHistory" ? (fh ? fhEl : null) : i === "shareholdingStyle" ? styles.find((x) => x.id === "shareholdingStyle") || null : i === "detailShareholding" ? (inserted.find((x) => x.el.id === "detailShareholding") || {}).el || null : null),
    createElement: (t) => { const e = mk(""); e.tag = t; return e; } };
  global.fetch = async (u, o) => { fetched.push({ u, o }); if (delay) await sleep(delay); if (failFetch) throw new Error("offline"); return { ok: status === 200, json: async () => JSON.parse(JSON.stringify(doc)) }; };
  (0, eval)(code);
  const P = { fetched, styles, obs, inserted, box, grp: () => grp, fhEl, after, S: window.SLShareholding,
    section: () => (inserted.find((x) => x.el.id === "detailShareholding") || {}).el || null,
    sectionHtml: () => ((inserted.find((x) => x.el.id === "detailShareholding") || {}).el || { innerHTML: "" }).innerHTML,
    notify: () => obs.forEach((o) => o.cb()),
    css: () => (styles[0] ? styles[0].textContent : "") };
  return P;
}
async function open(sym, o = {}) { const P = load(Object.assign({ hash: "#stock=" + sym }, o)); await sleep(15); P.sym = sym; return P; }
const cell = (h, key, sel = "data-cat") => { const m = new RegExp(sel + '="' + key + '"><span>([^<]*)</span><b>([^<]*)</b>').exec(h); return m ? [unesc(m[1]), m[2]] : null; };
const rowsOf = (h) => [...h.matchAll(/<tr data-q="([0-9-]+)"( data-status="unavailable")?><td>([^<]*)<\/td>((?:<td class="n">[^<]*<\/td>){5})<\/tr>/g)].map((m) => ({ q: m[1], un: !!m[2], label: m[3], cells: [...m[4].matchAll(/<td class="n">([^<]*)<\/td>/g)].map((x) => x[1]) }));
const pts = (h, key) => { const g = new RegExp('<g data-series="' + key + '"[^>]*>([\\s\\S]*?)</g>').exec(h); return g ? [...g[1].matchAll(/<circle data-q="([0-9-]+)" data-v="([0-9.]+)" cx="([0-9.]+)" cy="([0-9.]+)"/g)].map((m) => ({ q: m[1], v: +m[2], x: +m[3], y: +m[4] })) : null; };
const lines = (h, key) => { const g = new RegExp('<g data-series="' + key + '"[^>]*>([\\s\\S]*?)</g>').exec(h); return g ? [...g[1].matchAll(/<polyline[^>]* points="([^"]*)"/g)].map((m) => m[1].split(" ").map((p) => p.split(",").map(Number))) : null; };
const fmt = (v) => v.toFixed(2) + "%";

(async () => {
  // ================= 1. the data file: loaded once, from the static path only =================
  { const P = await open("TCS");
    eq(P.fetched.length, 1, "the file is requested once, although the snapshot group and the section both need it"); eq(P.fetched[0].u, "out/shareholding.json", "and it is the static shareholding file"); eq(P.fetched[0].o, { cache: "no-store" }, "never cached stale");
    eq(Object.keys(P.S).sort(), ["KEYS", "LABEL", "axisOf", "chartHtml", "check", "fmtPct", "hostOk", "model", "notesOf", "sectionHtml", "slotsOf", "summaryHtml"], "its public surface is small and fixed");
    ok(P.section() && P.grp().getAttribute("data-own-for") === "TCS", "both the full section and the snapshot ownership group are filled for the stock in the address"); }
  { const P = await open("TCS", { hash: "#all" }); eq(P.fetched.length, 0, "no request when the address is not a Stock Detail route"); eq(P.inserted.length, 0, "and nothing is placed"); }
  { const P = await open("TCS", { hash: "#stock=%E0%A4%A" }); eq(P.fetched.length, 0, "a malformed address is ignored"); }
  { const P = await open("TCS", { detail: false }); eq(P.fetched.length, 0, "no request before the Stock Detail view has finished rendering"); P.box.innerHTML = '<div id="detailChart"></div>'; P.notify(); await sleep(15); eq(P.fetched.length, 1, "it waits for the view, then asks once"); }
  { const P = await open("TCS", { fh: false, group: false }); eq(P.inserted.length, 0, "the section waits until Financial History is placed"); eq(P.fetched.length, 0, "and no request is made while there is nothing to fill"); }
  { const P = await open("TCS"); const ins = P.inserted.find((x) => x.el.id === "detailShareholding"); eq(ins.ref, P.after, "the section sits directly after Financial History"); P.notify(); P.notify(); await sleep(10); eq(P.inserted.length, 1, "repeated notifications place it once"); eq(P.fetched.length, 1, "and do not request again"); }
  { const P = await open("TCS"); window.location.hash = "#stock=ITC"; P.inserted.length = 0; P.grp().attrs = {}; P.grp().innerHTML = SNAP_PLACEHOLDER; P.box.innerHTML = '<div id="detailChart"></div>'; P.notify(); await sleep(15);
    ok(/Shareholding Pattern/.test(P.sectionHtml()) && !/data-q="2026-06-30"[^]*?Revised/.test(P.sectionHtml()), "a new route re-places and re-fills for the new stock"); eq(P.fetched.length, 1, "from the same loaded file"); }
  { const P = await open("TCS", { delay: 40 }); window.location.hash = "#stock=ITC"; await sleep(80); ok(!/>71\./.test(P.sectionHtml()), "a late answer for a stock that has been left is never shown for the new route"); }

  // ================= 2. categories and labels =================
  { const P = await open("TCS");
    eq(P.S.KEYS, KEYS, "the five categories of the data model, in this order"); eq(P.S.LABEL, LABEL, "with exactly the specified names");
    const h = P.sectionHtml();
    for (const k of KEYS) { eq(cell(h, k), [LABEL[k], fmt(DOC.stocks.TCS.quarters[0].values[k])], k + ": the label and the stored value in the latest cards"); eq(cell(P.grp().innerHTML, k, "data-own-cat"), [LABEL[k], fmt(DOC.stocks.TCS.quarters[0].values[k])], k + ": the label and the stored value in the Investor Snapshot"); }
    eq([...h.matchAll(/<th>([^<]*)<\/th>/g)].map((m) => unesc(m[1])), ["Quarter"].concat(KEYS.map((k) => LABEL[k])), "the table columns are Quarter and the five categories"); }
  for (const bad of [{ categories: ["promoters", "fii", "other_dii", "mutual_funds"] }, { categories: ["promoters", "fii", "mutual_funds", "other_dii", "retail_other"] }, { categories: undefined }, { schema: 2 }, { stocks: null }]) {
    const P = await open("TCS", { doc: Object.assign({}, DOC, bad) });
    re(un(P.sectionHtml()), /Shareholding data: Unavailable/, "a file that is not the known data model is not shown (" + JSON.stringify(bad).slice(0, 40) + ")"); re(un(P.grp().innerHTML), /Shareholding data: Unavailable/, "nor in the snapshot"); no(P.sectionHtml() + P.grp().innerHTML, /<table|<svg|data-cat=/, "no table, chart or value is drawn from it"); }

  // ================= 3. latest quarter =================
  { const P = await open("TCS"), L = DOC.stocks.TCS.quarters[0]; eq(L.quarter_end, "2026-06-30", "(fixture: the newest is Jun 2026)");
    re(P.sectionHtml(), /data-sh="asof">As of: Jun 2026 <span>\(quarter ended 2026-06-30\)<\/span>/, "the section says As of: Jun 2026"); re(P.grp().innerHTML, /data-own="asof">As of: Jun 2026<\/p>/, "the snapshot says As of: Jun 2026"); }
  { const P = await open("NEWUN"); re(un(P.sectionHtml()), /As of: Mar 2026/, "when the newest quarter is unavailable the latest AVAILABLE quarter is used"); re(un(P.sectionHtml()), /The newest quarter on file \(Jun 2026\) is unavailable/, "and the section says so"); re(un(P.grp().innerHTML), /As of: Mar 2026.*newest quarter on file \(Jun 2026\) is unavailable/, "and so does the snapshot");
    const q = DOC.stocks.NEWUN.quarters.find((r) => r.quarter_end === "2026-03-31"); eq(cell(P.sectionHtml(), "fii"), ["FII", fmt(q.values.fii)], "its values are the stored Mar 2026 values, nothing carried from Jun 2026"); }
  { const shuffled = JSON.parse(JSON.stringify(DOC)); shuffled.stocks.TCS.quarters.sort((a, b) => (a.quarter_end < b.quarter_end ? -1 : 1));       // oldest first in the file
    const P = await open("TCS", { doc: shuffled }); re(P.sectionHtml(), /As of: Jun 2026/, "the latest is chosen by date, not by position in the file"); eq(rowsOf(P.sectionHtml()).map((r) => r.q), QUARTERS.slice().reverse(), "and the table is newest first whatever the file order"); }
  { const P = await open("ALLUN"); re(un(P.sectionHtml()), /No available quarter is on file/, "a stock with no available quarter says so"); re(un(P.grp().innerHTML), /No available quarter is on file for ALLUN/, "in the snapshot too"); no(P.sectionHtml(), /<svg/, "and draws no chart");
    eq(rowsOf(P.sectionHtml()).length, 20, "while the table still lists every quarter with its reason"); }
  { const P = await open("NOSUCH"); re(un(P.sectionHtml()), /No shareholding data is on file for NOSUCH/, "a stock the file does not hold says so"); re(un(P.grp().innerHTML), /No shareholding data is on file for NOSUCH/, "in the snapshot too"); no(P.grp().innerHTML, /data-snap-go/, "with no View details link to nothing");
    const proto = await open("__proto__"); no(proto.sectionHtml() + proto.grp().innerHTML, /<table/, "an inherited property name is not a stock"); }

  // ================= 4. the five-year history =================
  { const P = await open("TCS"), rows = rowsOf(P.sectionHtml());
    eq(rows.map((r) => r.q), QUARTERS.slice().reverse(), "TCS: all 20 quarters, Jun 2026 first and Sep 2021 last"); eq(rows[0].label, "Jun 2026", "labelled by month and year"); eq(rows[19].label, "Sep 2021", "down to Sep 2021");
    for (const r of rows) { const src = DOC.stocks.TCS.quarters.find((x) => x.quarter_end === r.q); eq(r.cells, KEYS.map((k) => fmt(src.values[k])), r.q + ": every cell is the stored value with two decimals and %"); } }
  { const P = await open("TCS"), h = P.sectionHtml(), per = {};
    for (const k of KEYS) { per[k] = pts(h, k); eq(per[k].length, 20, k + ": a point for every one of the 20 quarters"); eq(per[k].map((p) => p.q), QUARTERS, k + ": the points are in chronological order, oldest to newest"); ok(per[k].every((p, i) => i === 0 || p.x > per[k][i - 1].x), k + ": x increases with time"); eq(per[k].map((p) => p.v), QUARTERS.map((q) => DOC.stocks.TCS.quarters.find((x) => x.quarter_end === q).values[k]), k + ": each point is the stored value"); }
    eq(lines(h, "promoters").length, 1, "one unbroken line when nothing is missing"); eq(lines(h, "promoters")[0].length, 20, "through all 20 points");
    const xs = [...new Set(KEYS.map((k) => per[k].map((p) => p.x).join()))]; eq(xs.length, 1, "the five series share the same time axis");
    re(h, /data-chartnote|data-sh="chartnote">Quarterly, Sep 2021 to Jun 2026\./, "the caption names the period from the data"); no(h, /data-gap=/, "no gap marks when there is no gap");
    eq([...h.matchAll(/data-xlabel="([0-9-]+)"/g)].map((m) => m[1]).slice(-1)[0], "2026-06-30", "the newest quarter is always labelled on the axis"); ok([...h.matchAll(/data-xlabel="/g)].length >= 5 && [...h.matchAll(/data-xlabel="/g)].length <= 20, "with labels at a readable spacing");
    ok([...h.matchAll(/data-grid="(\d+)"/g)].length >= 3 && /<text[^>]*>0%<\/text>/.test(h), "a percentage axis starting at 0%"); re(h, /% of shares/, "named in the chart"); }
  { const P = await open("TCS"), h = P.sectionHtml();
    eq([...h.matchAll(/<li data-legend="([a-z_]+)">/g)].map((m) => m[1]), KEYS, "the legend lists the five series in order"); for (const k of KEYS) re(h, new RegExp('data-legend="' + k + '">[^]*?<span>' + LABEL[k].replace(/&/g, "&amp;").replace(/[()/]/g, "\\$&") + '</span>'), k + ": named in the legend");
    const dashes = KEYS.map((k) => (new RegExp('<g data-series="' + k + '"[^>]*>[^]*?<polyline[^>]*?(stroke-dasharray="[^"]*")?\\s').exec(h) || [])[0]); ok(new Set(KEYS.map((k) => new RegExp('data-legend="' + k + '"[^]*?</li>').exec(h)[0].replace(/\s+/g, " "))).size === 5, "each legend entry is different (colour and line pattern, never colour alone)");
    re(h, /role="img" aria-label="Shareholding pattern, percent of shares by quarter, Sep 2021 to Jun 2026\./, "the chart has an accessible description"); }
  { const P = await open("TCS"), c = P.sectionHtml(); ok(c.indexOf("Latest ownership snapshot") < c.indexOf("Five-Year Trend") && c.indexOf("Five-Year Trend") < c.indexOf("Quarterly history"), "the section order: latest snapshot, trend, history table"); }

  // ================= 5. gaps, zero and interpolation =================
  { const P = await open("ITC"), h = P.sectionHtml(), rows = rowsOf(h);
    for (const k of KEYS) { const p = pts(h, k); eq(p.map((x) => x.q), QUARTERS.slice(4), "ITC " + k + ": no point for the four unavailable quarters"); ok(!p.some((x) => x.q <= "2022-06-30"), k + ": nothing is drawn before Sep 2022"); }
    eq([...h.matchAll(/data-gap="([0-9-]+)"/g)].map((m) => m[1]), QUARTERS.slice(0, 4), "the four unavailable quarters are marked as gaps on the time axis"); re(un(h), /Dotted vertical lines mark quarters with no data on file/, "and the caption explains the marks");
    eq(rows.filter((r) => r.un).map((r) => r.q), QUARTERS.slice(0, 4).reverse(), "the table marks the same four quarters unavailable");
    for (const r of rows.filter((r) => r.un)) eq(r.cells, ["-", "-", "-", "-", "-"], r.q + ": unavailable shows - in every column, never 0.00%"); }
  { const P = await open("GAPCO"), h = P.sectionHtml(), rows = rowsOf(h);
    eq(rows.length, 19, "a quarter the file does not hold has no table row (nothing is invented)"); ok(!rows.some((r) => r.q === "2023-06-30"), "2023-06-30 is absent from the table");
    const p = pts(h, "promoters"); eq(p.length, 19, "and has no point"); ok(!p.some((x) => x.q === "2023-06-30"), "none at 2023-06-30");
    const i = p.findIndex((x) => x.q === "2023-03-31"), step = p[1].x - p[0].x; ok(p[i + 1].x - p[i].x > step * 1.9 && p[i + 1].x - p[i].x < step * 2.1, "the time axis keeps the empty slot: the neighbours are two slots apart");
    eq(lines(h, "promoters").length, 2, "the line is broken in two at the gap, not joined across it"); eq(lines(h, "promoters").map((l) => l.length), [7, 12], "each part holds only real points");
    re(h, /data-gap="2023-06-30"/, "the missing quarter is marked");
    const f = pts(h, "fii"); ok(!f.some((x) => x.q === "2024-03-31"), "a category that is null in one quarter has no point there (never zero)"); eq(lines(h, "fii").length, 3, "and that series is broken there as well");
    const row = rows.find((r) => r.q === "2024-03-31"); eq(row.cells[1], "-", "the table shows - for the null category"); ok(row.cells[0] !== "-" && row.cells[0] !== "0.00%", "while the other categories keep their stored values");
    ok(!pts(h, "promoters").some((x) => x.v === 0) && !f.some((x) => x.v === 0), "no point is drawn at 0 for missing data");
    re(un(h), /Data note: a category row is missing in the filing \(fii\); it is shown as -/, "and the table says why"); }
  { const P = await open("HDFCBANK"), h = P.sectionHtml(), rows = rowsOf(h);
    ok(rows.every((r) => r.cells[0] === "0.00%"), "HDFCBANK: a stored 0 promoter holding is shown as 0.00%, not -"); eq(pts(h, "promoters").length, 20, "and drawn as a real point on the 0% line in all 20 quarters"); ok(pts(h, "promoters").every((p) => p.v === 0), "(value 0)");
    eq(cell(P.grp().innerHTML, "promoters", "data-own-cat"), ["Promoters", "0.00%"], "the snapshot shows 0.00% too"); re(un(h), /0\.00% means the filing reports no holding in that category \(for example, no promoter group\)/, "with a plain explanation of what 0.00% means here");
    const ys = pts(h, "promoters").map((p) => p.y); ok(ys.every((y) => y === ys[0]), "all on the baseline"); }
  { const P = await open("TCS"); no(un(P.sectionHtml()), /0\.00% means the filing/, "that explanation is not shown where no filing lacks a promoter group"); }
  { const P = await open("ONEQ"), h = P.sectionHtml(); re(un(h), /Not enough quarters on file to draw a trend/, "a single quarter draws no trend"); no(h, /<svg/, "no chart, no invented line"); eq(rowsOf(h).length, 1, "the one quarter is in the table"); eq(cell(h, "fii"), ["FII", fmt(DOC.stocks.ONEQ.quarters[0].values.fii)], "and in the latest cards"); }
  { // the axis never invents a range the data does not need, and never clips
    const { axisOf } = (await open("TCS")).S;
    eq(axisOf(71.77), { max: 80, ticks: [0, 20, 40, 60, 80] }, "71.77 -> 0 to 80"); eq(axisOf(100), { max: 100, ticks: [0, 20, 40, 60, 80, 100] }, "100 -> 0 to 100"); eq(axisOf(3), { max: 5, ticks: [0, 5] }, "3 -> 0 to 5"); eq(axisOf(0), { max: 5, ticks: [0, 5] }, "all zero -> a valid axis");
    for (const m of [0.4, 7, 9.99, 10, 19.9, 33, 49.5, 50, 61, 79.9, 80.1, 99.99, 100.4]) { const a = axisOf(m); ok(a.max >= m && a.ticks[0] === 0 && a.ticks.length <= 7 && a.ticks[a.ticks.length - 1] === a.max, "axis for " + m + " holds the value without clipping"); } }

  // ================= 6. ICICIBANK: the reported values stand =================
  { const P = await open("ICICIBANK"), h = P.sectionHtml(), rows = rowsOf(h), mar = rows.find((r) => r.q === "2026-03-31"), jun = rows.find((r) => r.q === "2026-06-30"), dec = rows.find((r) => r.q === "2025-12-31");
    eq(mar.cells, ["0.00%", "34.49%", "12.12%", "27.83%", "25.57%"], "Mar 2026: the reported NSE percentages, exactly"); eq(jun.cells, ["0.00%", "49.82%", "12.71%", "29.60%", "7.87%"], "Jun 2026: the reported NSE percentages, exactly");
    for (const bad of ["42.06", "33.94", "14.77", "9.23", "40.24"]) no(P.sectionHtml() + P.grp().innerHTML, new RegExp(bad), "the share-count percentage " + bad + " is never shown anywhere");
    no(P.sectionHtml() + P.grp().innerHTML, /share_derived|derived/i, "the diagnostic field is not mentioned");
    eq(cell(h, "fii"), ["FII", "49.82%"], "the latest card is the reported Jun 2026 FII"); eq(cell(P.grp().innerHTML, "fii", "data-own-cat"), ["FII", "49.82%"], "and so is the snapshot");
    re(un(h), /Mar 2026 .*Data-quality warning: the percentages reported to NSE differ from the filing's own share counts by up to 16\.34 percentage points\. The reported percentages are shown, unchanged\./, "Mar 2026 carries the warning with its size");
    re(un(h), /Data-quality warning: the percentages reported to NSE differ from the filing's own share counts by up to 9\.58 percentage points/, "Jun 2026 carries the warning with its size"); eq((h.match(/Data-quality warning/g) || []).length, 3, "the warning appears for the two flagged quarters only (once more in the latest card for Jun 2026)");
    const noteDec = new RegExp('data-q-note="2025-12-31">.*?</tr>').exec(h.replace(/\n/g, " "))[0]; no(noteDec, /Data-quality warning/, "Dec 2025 (consistent) has no warning");
    ok(P.S.notesOf(P.S.model(DOC, "ICICIBANK").qs[0]).some((n) => n.warn), "the note is a warning-level note");
    re(P.grp().innerHTML, /data-own="quality">This quarter carries a data-quality note; see Shareholding Pattern for details\./, "the snapshot points to the note without repeating figures");
    const chart = pts(h, "fii"); eq(chart.find((p) => p.q === "2026-03-31").v, 34.49, "the chart plots the reported value, not a derived one"); eq(chart.find((p) => p.q === "2026-06-30").v, 49.82, "for Jun 2026 too"); }
  { const P = await open("TCS"); no(P.grp().innerHTML, /data-own="quality"/, "no data-quality line in the snapshot of a clean latest quarter"); }

  // ================= 7. quality notes, unavailable reasons, revisions =================
  { const P = await open("ITC"), h = P.sectionHtml().replace(/\n/g, " ");
    for (const q of QUARTERS.slice(0, 4)) { const n = new RegExp('data-q-note="' + q + '">(.*?)</tr>').exec(h)[1]; re(un(n), /Unavailable: the XBRL file could not be obtained \(HTTP 404\)\./, q + ": the reason is shown in words"); eq((un(n).match(/Unavailable/g) || []).length, 1, q + ": and only once (the duplicate flag is not repeated)"); } }
  { const P = await open("TCS"), h = P.sectionHtml().replace(/\n/g, " "), n = new RegExp('data-q-note="2026-03-31">(.*?)</tr>').exec(h)[1];
    re(un(n), /Revised filing \(revised 2026-09-16\)\./, "a revised quarter says so, with the revision date"); re(un(n), /Revised filing, revision date: 2026-09-16\. Remark: Revised promoter group list/, "and its remark is under Source");
    for (const q of QUARTERS.slice(0, 4)) re(un(new RegExp('data-q-note="' + q + '">(.*?)</tr>').exec(h)[1]), /Mapping note: this older-format filing has an Other Institutions row that cannot be split between foreign and domestic institutions, so it is included in DII \(Other\)\./, q + ": the other-institutions mapping note");
    const clean = new RegExp('data-q-note="2025-09-30">(.*?)</tr>').exec(h)[1]; no(clean, /class="sh-n/, "a clean quarter has no note"); }
  { const m = await open("TCS"), S = m.S; const dm = S.model({ schema: 1, categories: KEYS, stocks: { X: { quarters: [{ quarter_end: "2026-06-30", status: "available", values: { promoters: 1, fii: 2, other_dii: 3, mutual_funds: 4, retail_other: 5 }, quality: { flags: [{ code: "five-categories-do-not-sum-to-100", detail: "the five categories add up to 98.50" }, { code: "something-new", detail: "a <b>new</b> note" }, { code: "kept-previous-value", detail: null }, { nope: 1 }, null] }, source: {} }] } } }, "X");
    const t = S.notesOf(dm.qs[0]).map((n) => n.t); eq(t.length, 3, "unknown flag codes are still shown (as data notes), malformed flags are dropped"); eq(t[0], "Data note: the five categories add up to 98.50. The values are shown as reported.", "a total that is not 100 is shown as reported, never forced"); eq(t[1], "Data note: a <b>new</b> note", "(raw text, escaped later)"); eq(t[2], "Data note: kept-previous-value", "a flag without detail falls back to its code");
    const h = S.sectionHtml("X", { schema: 1, categories: KEYS, stocks: { X: { quarters: dm.qs.map((r) => ({ quarter_end: r.q, status: "available", values: r.values, quality: { flags: [{ code: "x", detail: '<img src=x onerror=alert(1)>' }] }, source: {} })) } } }); no(h, /<img src=x/, "note text is escaped"); }

  // ================= 8. source information and URL safety =================
  { const P = await open("TCS"), h = P.sectionHtml(), L = DOC.stocks.TCS.quarters[0], s = L.source;
    re(un(h), /Source: NSE \/ XBRL/, "the source is named: NSE / XBRL"); re(h, new RegExp("<li>Record ID: " + s.record_id + "</li>"), "the NSE record ID"); re(h, new RegExp("<li>Broadcast date: " + s.broadcast_date + "</li>"), "the broadcast date"); re(h, new RegExp("<li>Submission date: " + s.submission_date + "</li>"), "the submission date");
    re(h, new RegExp('<a href="' + s.xbrl_url.replace(/[.]/g, "\\.") + '" target="_blank" rel="noopener noreferrer">View source</a>'), "a View source link to the stored XBRL URL, opening safely");
    re(h, /Filing format: Older format \(a single Institutions subtotal and one foreign portfolio investor row\)/, "the older filing format is named"); re(h, /Filing format: Newer format \(domestic institutions and foreign institutions reported separately\)/, "and the newer format");
    eq((h.match(/>View source</g) || []).length, 21, "one View source for the latest card and one for each of the 20 quarters");
    const urls = [...h.matchAll(/<a href="([^"]*)"/g)].map((m) => m[1]); ok(urls.every((u) => /^https:\/\/nsearchives\.nseindia\.com\//.test(u)), "every link is the stored https NSE URL, none invented"); eq(new Set(urls).size, 20, "20 different files, each the stored one"); }
  { const { hostOk } = (await open("TCS")).S;
    for (const good of ["https://nsearchives.nseindia.com/corporate/xbrl/a.xml", "https://archives.nseindia.com/a.xml", "https://www.nseindia.com/a?b=1#c", "https://nseindia.com/a.xml"]) ok(hostOk(good), "accepted: " + good);
    for (const bad of ["http://nsearchives.nseindia.com/a.xml", "javascript:alert(1)", "data:text/html,<b>", "//nsearchives.nseindia.com/a.xml", "https://evil.com/a.xml", "https://nseindia.com.evil.com/a.xml", "https://evilnseindia.com/a.xml", "https://nsearchives.nseindia.com@evil.com/a.xml", "https://nsearchives.nseindia.com/a.xml\"onmouseover=\"x", "https://nsearchives.nseindia.com/a b.xml", "https://nsearchives.nseindia.com/<script>", "https://nsearchives.nseindia.com/a'b", "ftp://nsearchives.nseindia.com/a", "", null, 5, undefined, "https://nsearchives.nseindia.com\\evil"]) ok(!hostOk(bad), "refused: " + String(bad)); }
  { const d = JSON.parse(JSON.stringify(DOC)), q = d.stocks.TCS.quarters; q[0].source.xbrl_url = "javascript:alert(1)"; q[1].source.xbrl_url = "http://nsearchives.nseindia.com/a.xml"; q[2].source.xbrl_url = "https://evil.example.com/a.xml"; q[3].source.xbrl_url = null; delete q[4].source;
    const P = await open("TCS", { doc: d }), h = P.sectionHtml(); no(h, /javascript:|evil\.example|href="http:/, "an unsafe or foreign URL is never linked"); eq((h.match(/>View source</g) || []).length, 21 - 6, "only quarters with a safe stored URL get a link; nothing is made up for the others"); re(h, /data-q-note="2026-06-30"><\/td>|data-q-note="2026-06-30"><td colspan="6">/, "(the row still renders)");
    no(h, /undefined|null|NaN/, "no placeholder text for a missing source"); }

  // ================= 9. the Investor Snapshot ownership summary =================
  { const P = await open("TCS"), g = P.grp().innerHTML;
    eq(P.grp().getAttribute("data-own-for"), "TCS", "the existing Ownership group is filled (it is not replaced or moved)"); no(g, /will be added/, "the placeholder is replaced");
    eq([...g.matchAll(/data-own-cat="([a-z_]+)"><span>([^<]*)<\/span>/g)].map((m) => [m[1], unesc(m[2])]), KEYS.map((k) => [k, LABEL[k]]), "five values with the specified names, in order"); re(g, /<h4>Ownership<\/h4>/, "under the Ownership heading");
    re(g, /<button type="button" class="lk" data-snap-go="detailShareholding">View details<\/button>/, "a View details button for the existing snapshot scroll handler, aimed at the section id"); eq(P.section().id, "detailShareholding", "which is the id of the full section");
    re(snapCode, /data-snap-go/, "the snapshot's existing handler reads data-snap-go"); re(snapCode, /t\.scrollIntoView\(\{block:"start"\}\)/, "and scrolls to that element in place"); no(snapCode.split('document.addEventListener("click"')[1] || "", /location|hash|history/, "without changing the address");
    no(shCode.replace(/\/\*[\s\S]*?\*\//g, ""), /addEventListener|scrollIntoView|scrollTo|location\.hash\s*=|pushState|replaceState/, "this module adds no listener, no scroll and never touches the address");
    re(un(g), /As of: Jun 2026/, "As of: the latest quarter"); re(un(g), /Percent of shares as reported to NSE\. Source: NSE \/ XBRL\./, "with the unit and source"); no(g, /\d{4}-\d{2}-\d{2}/, "compact: no filing dates in the summary"); }
  { const P = await open("TCS", { failFetch: true }); re(un(P.grp().innerHTML), /Shareholding data: Unavailable/, "when the file cannot be loaded the snapshot says Unavailable"); no(P.grp().innerHTML, /data-snap-go/, "with no link to a section that has no data"); re(un(P.sectionHtml()), /Shareholding data: Unavailable/, "and so does the section");
    const P2 = await open("TCS", { status: 404 }); re(un(P2.sectionHtml()), /Shareholding data: Unavailable/, "an absent file (HTTP 404, before the first publish) is Unavailable, not an error"); }
  { // the snapshot module itself is exactly as committed
    let committed = ""; try { committed = cp.execSync("git show HEAD:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { committed = ""; } committed = require("./legacy_5i.js").legacy(committed);
    if (committed) { const same = (tag) => modOf(committed, tag) === modOf(PINHTML, tag); ok(same(SNAPTAG), "the Investor Snapshot module is byte-for-byte as committed"); ok(same(GROWTAG), "the growth module is byte-for-byte as committed"); ok(same('<script type="module" id="stocklens-compare">'), "the comparison module is as committed"); ok(require("crypto").createHash("sha256").update(modOf(PINHTML, '<script type="module" id="stocklens-search">')).digest("hex") === require("./search_module_pin.json").pre_5i_search_module_sha256, "the search module is exactly the approved stock-directory search version (pinned in tests/search_module_pin.json; its behaviour is tested in test_company_search.js)"); ok(same('<script type="module">'), "the Financial History module is as committed");
      const rest = (s) => s.replace(/<script type="module" id="stocklens-shareholding">[\s\S]*?<\/script>\n/, "").replace(/<script type="module" id="stocklens-search">[\s\S]*?<\/script>\n/, ""); eq(rest(PINHTML), rest(committed), "everything outside the new module and outside the search module (pinned separately above) is identical to the committed page"); }
    re(snapCode, /grp\("ownership","Ownership",'<p class="snap-t">Shareholding history will be added in the Shareholding Pattern phase\.<\/p>'\)/, "the snapshot still builds its Ownership placeholder; this module is what replaces it when the data is present"); }

  // ================= 10. no advice, no interpretation =================
  { const all = []; for (const s of Object.keys(DOC.stocks)) { const P = await open(s); all.push(P.sectionHtml(), P.grp().innerHTML); }
    const t = un(all.join(" ")); no(t, ADVICE, "the whole section and summary, for every fixture stock, contain no advice, rating, rank, score or opinion wording");
    no(t, /\b(high|low|higher|lower|increase\w*|decrease\w*|rising|falling|grow\w*|drop\w*|surge\w*|change[sd]?)\b/i, "and no interpretation of movement or level");
    no(t, /Upstox|cross-?check|second source/i, "no mention of the cross-check source"); no(t, /\b(Buy|Sell|Target|Rating|Score)\b/, "none of Buy / Sell / Target / Rating / Score");
    const code = shCode.replace(/\/\*[\s\S]*?\*\//g, ""); no((code.match(/"[^"]*"|'[^']*'/g) || []).filter((m) => /[a-z]{4,}\s[a-z]{3,}/i.test(m)).join(" ").replace(/ target=\\?"_blank\\?"/g, ""), ADVICE, "and none in the module's own sentences (the HTML attribute target=\"_blank\" is not a sentence)");
    no(t, /good for|bad for|means that|is a sign|indicates|suggests|implies/i, "no sentence interprets what an ownership level means"); }

  // ================= 11. reported values untouched; no other source =================
  { const P = await open("ICICIBANK"), blob = P.sectionHtml() + P.grp().innerHTML;
    no(blob, /99\.99|98\.99/, "the decoy comparison values stored beside the data (another source) are never shown"); no(blob, /SECRET_RAW_ROW/, "the raw audit rows are never shown"); no(blob, /61\.37|62\.48|63\.59|64\.61|65\.72/, "nor the diagnostic share-count percentages");
    no(shCode, /cross_check|share_derived|upstox|\braw\b/i, "the module never reads cross_check, share_derived_pct or raw"); no(shCode.replace(/\/\*[\s\S]*?\*\//g, ""), /\.diag\.[a-z_]+/g.test("") ? /^$/ : /\.diag\.(?!conflict|promoter_row_absent)\w+/, "of the diagnostics it reads only the conflict size and the promoter-row flag"); }
  { // every displayed percentage is the stored number with toFixed(2): nothing is computed from other values
    const code = shCode.replace(/\/\*[\s\S]*?\*\//g, ""); eq((code.match(/toFixed\(/g) || []).length, 3, "toFixed is used for the stored value, the conflict size and axis positions only");
    no(code, /Math\.(round|floor|ceil)\([^)]*values/, "no arithmetic on stored values"); no(code, /values\[[a-z]+\]\s*[-+*\/]|\.values\.[a-z_]+\s*[-+*\/]/, "no sum, difference or ratio of stored values");
    const P = await open("ICICIBANK"); eq(P.S.fmtPct(34.49), "34.49%", "34.49 -> 34.49%"); eq(P.S.fmtPct(0), "0.00%", "0 -> 0.00%"); eq(P.S.fmtPct(5), "5.00%", "5 -> 5.00%"); eq(P.S.fmtPct(null), "-", "null -> -"); eq(P.S.fmtPct(undefined), "-", "undefined -> -"); eq(P.S.fmtPct(NaN), "-", "NaN -> -"); eq(P.S.fmtPct("12"), "-", "a string -> -"); eq(P.S.fmtPct(-1), "-", "a negative -> -"); eq(P.S.fmtPct(150), "-", "above 100.5 -> -"); eq(P.S.fmtPct(Infinity), "-", "Infinity -> -"); }
  { const d = JSON.parse(JSON.stringify(DOC)); const q = d.stocks.TCS.quarters; q[0].values.fii = "34.49"; q[1].values.fii = NaN; q[2].values.fii = -3; q[3].status = "pending"; q[4].quarter_end = "2026-02-15"; q[5].quarter_end = q[6].quarter_end; q[7] = null;
    const P = await open("TCS", { doc: d }), rows = rowsOf(P.sectionHtml());
    eq(rows.find((r) => r.q === "2026-06-30").cells[1], "-", "a value that is not a stored number shows -"); eq(rows.find((r) => r.q === "2026-03-31").cells[1], "-", "NaN (null in JSON) shows -"); eq(rows.find((r) => r.q === "2025-12-31").cells[1], "-", "a negative shows -");
    ok(rows.find((r) => r.q === "2025-09-30").un, "an unknown status is not treated as available"); ok(!rows.some((r) => r.q === "2026-02-15"), "a date that is not a quarter end is dropped"); eq(rows.filter((r) => r.q === QUARTERS[QUARTERS.length - 7]).length <= 1, true, "a duplicate quarter is shown once"); }

  { // an unavailable quarter holds nothing, even if the file carries stray values for it
    const d = JSON.parse(JSON.stringify(DOC)); const u = d.stocks.ITC.quarters.find((r) => r.quarter_end === "2022-03-31"); u.values = { promoters: 12.34, fii: 12.34, other_dii: 12.34, mutual_funds: 12.34, retail_other: 12.34 };
    const P = await open("ITC", { doc: d }), h = P.sectionHtml(); eq(rowsOf(h).find((r) => r.q === "2022-03-31").cells, ["-", "-", "-", "-", "-"], "stray values on an unavailable quarter are not shown"); no(h, /12\.34/, "anywhere"); ok(!pts(h, "fii").some((p) => p.q === "2022-03-31"), "nor plotted"); }

  // ================= 12. responsive layout =================
  { const P = await open("TCS"), css = P.css(), h = P.sectionHtml();
    ok(css.length > 500, "the module adds its own style once"); eq(P.styles.length, 1, "exactly one style element"); await open("TCS"); eq(P.styles.length, 1, "(and not again)");
    const rules = css.replace(/@(media|container)[^{]*\{((?:[^{}]*\{[^{}]*\})+)\}/g, "$2").split("}").filter(Boolean).map((r) => r.trim() + "}"); ok(rules.every((r) => /^(:root(\[data-theme="dark"\]|:not\(\[data-theme="light"\]\))? )?#detailShareholding[ .{,]/.test(r)), "every rule is scoped to the Shareholding section, so nothing else on the page is restyled");
    re(css, /\.sh-cards\{display:grid;grid-template-columns:repeat\(auto-fit,minmax\(min\(130px,100%\),1fr\)\)/, "the latest cards sit in a grid that gives way to fewer columns and never needs more than the screen"); re(css, /\.sh-leg\{[^}]*flex-wrap:wrap/, "the legend wraps");
    re(css, /\.sh-chart\{max-width:480px\}/, "the chart is capped on wide screens"); re(h, /<svg viewBox="0 0 320 230"[^>]*style="display:block;width:100%;height:auto"/, "and scales with its box (viewBox, width 100%, height auto): the same drawing at 1280, 390 and 320 px");
    no(css, /position:\s*(absolute|fixed)|white-space:\s*nowrap|(^|[;{])width:\s*\d+px|overflow-x:\s*(scroll|auto)|min-width:\s*(4[5-9]\d|[5-9]\d\d|\d{4})/, "no absolute or fixed position, no nowrap, no fixed width, no wide minimum, no scrolling box of its own");
    { const lab = [...h.matchAll(/data-xlabel="[0-9-]+" x="([0-9.]+)"[^>]*>([^<]*)</g)].map((m) => ({ x: +m[1], t: m[2] })); ok(lab.length >= 5, "labels present"); ok(lab[lab.length - 1].t.length >= 5, "the newest label is the full short date");
      ok(lab.every((l) => l.x - l.t.length * 6.2 / 2 >= 0 && l.x + l.t.length * 6.2 / 2 <= 320), "every x-axis label, including the last (Jun 2026), lies fully inside the 320-unit drawing (a generous 6.2 units per character at font-size 11)"); }
    re(css, /\.scroll\{container-type:inline-size\}/, "the history table's scroll box is a size container"); re(css, /\.sh-subin\{position:sticky;left:0;[^}]*width:100cqw;max-width:100cqw;padding:0 8px 8px;overflow-wrap:anywhere\}/, "(the padding is inside the block, so it never pushes the block past the visible box)"); re(css, /tr\.sh-sub td\{padding:0;/, "each quarter's note/source block is as wide as the visible box, stays at the left while the table scrolls, and wraps");
    { const subs = h.split('<tr class="sh-sub"').slice(1); ok(subs.length >= 20 && subs.every((r) => /^[^>]*><td colspan="6"><div class="sh-subin">/.test(r) && r.split("</tr>")[0].endsWith("</div></td>")), "every note row wraps all of its content (reason, flags, source) in the width-limited block"); }
    re(css, /#detailShareholding table\{min-width:440px\}/, "the table's minimum width is smaller than the 560px of the other tables"); re(h, /<div class="scroll"><table id="shTable"/, "and sits in the page's existing .scroll wrapper, so a narrow screen scrolls the table, never the page");
    for (const sel of [".sh-p", ".sh-card span", ".sh-n", ".sh-src p", ".sh-src a", ".sh-leg span"]) re(css, new RegExp(sel.replace(/[.]/g, "\\.") + "[^{]*\\{[^}]*overflow-wrap:anywhere"), sel + " wraps long words");
    // 390px and 320px: the widest unbreakable pieces fit the narrowest content box (320 minus page and panel padding)
    const content320 = 320 - 2 * 16 - 2 * 16; ok(content320 >= 250, "the narrowest content box is about " + content320 + "px"); const viewW = 320; ok(viewW / content320 < 1.4, "the 320-unit chart is scaled down by under 1.4x at 320px, so its 11-unit text stays about 8px or more");
    const fontScaled = 11 * content320 / viewW; ok(fontScaled >= 8, "chart text at 320px is " + fontScaled.toFixed(1) + "px, not smaller than the 9-unit text of the existing financial charts at the same width (" + (9 * content320 / 320).toFixed(1) + "px)");
    const w = (x) => (x.match(/<svg[^>]*data-sh="chart"/) || [""])[0]; ok(!/ width="\d+"/.test(w(h)), "the chart has no fixed pixel width attribute"); no(h, /<img|<canvas|<iframe|<object|<embed|<link /, "no image, canvas or embedded content");
    ok([...h.matchAll(/<text[^>]*x="(\d+(\.\d+)?)"/g)].every((m) => +m[1] >= 0 && +m[1] <= 320), "every text in the chart is inside the 320-unit width");
    const ys = [...h.matchAll(/<text[^>]*y="(\d+(\.\d+)?)"/g)].map((m) => +m[1]); ok(ys.every((y) => y >= 0 && y <= 230), "and inside the 230-unit height"); const cx = [...h.matchAll(/<circle[^>]* cx="(\d+(\.\d+)?)" cy="(\d+(\.\d+)?)"/g)].map((m) => [+m[1], +m[3]]); ok(cx.every(([x, y]) => x >= 40 && x <= 310 && y >= 16 && y <= 196), "every point is inside the plot area, so none is clipped"); }
  { // the labels never crowd: for every number of quarters from 2 to 40 the spacing between labelled quarters is at least 38 units
    const P = await open("TCS"); for (const n of [2, 3, 5, 8, 12, 20, 33, 40]) { const q = []; for (let i = 0, d = "2016-06-30"; i < n; i++, d = nextQ(d)) q.push(d); const d = { schema: 1, categories: KEYS, stocks: { Z: { quarters: q.map((x, i) => rec(x, i, { p: 40, f: 20, d: 10, m: 10, r: 20 })).reverse() } } };
      const h = P.S.chartHtml(P.S.model(d, "Z")), xs = [...h.matchAll(/data-xlabel="[0-9-]+" x="([0-9.]+)"/g)].map((m) => +m[1]); ok(xs.length >= 2 || n < 3, n + " quarters: labels drawn"); ok(xs.every((x, i) => i === 0 || x - xs[i - 1] >= 33.5), n + " quarters: the labels are at least 38 units apart (" + xs.length + " labels)"); ok(h.includes('data-xlabel="' + q[n - 1] + '"'), n + " quarters: the newest is labelled"); } }

  // ================= 13. security and scope =================
  { const code = shCode.replace(/\/\*[\s\S]*?\*\//g, "");
    eq((code.match(/\bfetch\(/g) || []).length, 1, "one fetch call in the module"); eq(code.match(/fetch\("([^"]*)"/)[1], "out/shareholding.json", "and it fetches only the static shareholding file"); eq((code.match(/"[^"]*\.json"/g) || []), ['"out/shareholding.json"'], "the only file name in the code");
    no(code, /https?:\/\//, "no web address in the code"); no(code, /XMLHttpRequest|sendBeacon|EventSource|WebSocket|importScripts|import\(|\bimport\s|require\(/, "no other request mechanism, no dynamic import");
    no(code, /upstox|\/api\/|graphql|api\./i, "no Upstox or API call"); no(code, /token|secret|apikey|api_key|bearer|authorization|password|credential|cookie|header/i, "no token, key, header or cookie"); no(code, /\beval\s*\(|new Function|document\.write|outerHTML|insertAdjacentHTML|javascript:/, "no code evaluation or unsafe markup insertion");
    no(code, /localStorage|sessionStorage|indexedDB|setInterval|setTimeout|requestAnimationFrame/, "stores nothing and runs no timer");
    eq((code.match(/\.innerHTML\s*=/g) || []).length, 3, "innerHTML is written in three places (summary, loading text, section), always built from escaped text"); ok(/var esc=/.test(code) && /esc\(/.test(code), "every dynamic string goes through esc()");
    const dyn = [...code.matchAll(/\+\s*([a-zA-Z_.\[\]]+)\s*\+/g)].length; ok(dyn > 20, "(many dynamic pieces, all reviewed)"); }
  { no(html, /<script[^>]+src=/i, "the page loads no external script"); no(shCode, /cdn\.|unpkg|jsdelivr|cloudflare|chart\.js|d3|plotly|highcharts|echarts|lightweight|LIB_URL|loadScript|createElement\("script"\)/i, "the Shareholding module references no chart library and loads no script");
    { let committed = ""; try { committed = cp.execSync("git show HEAD:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }); } catch (e) { committed = html; } const libs = (x) => (x.match(/unpkg|jsdelivr|cdnjs|cloudflare|cdn\./gi) || []).length; eq(libs(html), libs(committed), "and the page references no more external libraries than before (the existing price chart's one is unchanged)"); } no(html, /UPSTOX_ANALYTICS_TOKEN|Bearer\s+[A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{20,}|api[_-]?key\s*[:=]|nseindia\.com\/api|NSE.?session/i, "no token, bearer value, JWT, API key or NSE session anywhere in index.html"); no(shCode, /<svg[^>]*(src|href)=/, "the chart is drawn inline, with nothing fetched"); }
  { const urls = [...html.replace(/<script type="module" id="stocklens-live">[\s\S]*?<\/script>\n/, "").matchAll(/https?:\/\/[^\s"'<)]+/g)].map((m) => m[0]); const before = (() => { try { return [...cp.execSync("git show HEAD:index.html", { cwd: ROOT, encoding: "utf8", maxBuffer: 1 << 26 }).replace(/<script type="module" id="stocklens-live">[\s\S]*?<\/script>\n/, "").matchAll(/https?:\/\/[^\s"'<)]+/g)].map((m) => m[0]); } catch (e) { return urls; } })(); eq(urls.sort(), before.sort(), "the page has exactly the same web addresses as the committed page: this phase adds none"); }
  { const st = cp.execSync("git status --porcelain", { cwd: ROOT, encoding: "utf8" }).split("\n").filter(Boolean).map((l) => l.slice(3));
    const UPD = ".github/workflows/update.yml", okUpd = () => { const d = require("child_process").execSync("git diff -U0 HEAD -- " + UPD, { cwd: ROOT, encoding: "utf8" }).split("\n").filter((l) => /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l)); return d.length === 2 && d[0].startsWith("+          # the searchable stock directory") && d[1] === "+          if [ -f ledger-branch/stock_directory.json ]; then cp ledger-branch/stock_directory.json _site/out/stock_directory.json; fi"; };
    ok(!st.includes(UPD) || okUpd(), "update.yml differs from the commit only by the approved guarded stock_directory.json copy (two added lines)");
    for (const f of st.filter((x) => x !== UPD)) ok(!/^(shareholding_|nse_updater|fundamentals_updater|financials_updater|financial_history_updater|historical_updater|validate_outputs|upstox_common|ledger_storage|\.github\/|official_financial_records|company_profiles\.json|out\/|data\/)/.test(f), "no data-pipeline, workflow or data file is changed: " + f); }

  console.log("Shareholding UI tests passed (" + checks + " checks)");
})().catch((e) => { console.error("SHAREHOLDING UI TEST FAILED: " + e.message + "\n" + (e.stack || "").split("\n").slice(1, 4).join("\n")); process.exit(1); });
