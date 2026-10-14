// Run: node tests/test_quarterly_ui.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the read-only Quarterly Results section. Its input is built by the real quarterly_results_updater (through test_quarterly_results' fixtures), so the page is tested against the shape the updater really writes.
const fs = require("fs"), assert = require("assert"), cp = require("child_process"), vm = require("vm");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const TAG = '<script type="module" id="stocklens-quarterly">';
eq(html.split(TAG).length, 2, "exactly one Quarterly Results module");
const code = html.split(TAG)[1].split("</script>")[0];

// the document the real updater writes for a stock (two stocks, AAA with five quarters)
const py = "import sys,json; sys.path.insert(0,'.'); import test_quarterly_results as t, quarterly_results_updater as q\n" +
  "c={}\nfor s in ('AAA','BBB'):\n r=q.empty_record(); q.merge_quarters(r,q.parse_quarterly(t.GOOD),'consolidated','2026-08-01'); r['last_checked']=r['last_success']='2026-08-01'; c[s]=r\n" +
  "d,m,p=q.build_outputs(t.ENTRIES,c,'2026-08-01'); print(json.dumps(d))";
const DOCS = JSON.parse(cp.execFileSync("python3", ["-c", py], { cwd: ROOT, encoding: "utf8" }));
ok(DOCS.AAA && DOCS.BBB, "the updater produced shards");

// run the module against a stub page
function boot(hash, files) {
  const els = {}, fetched = [];
  const win = { location: { hash }, SLQuarterly: null };
  const ctx = { window: win, document: { getElementById: (i) => els[i] || null, head: null, createElement: () => ({}) }, fetch: (u) => { fetched.push(u); const f = files[u]; return Promise.resolve(f === undefined ? { ok: false } : { ok: true, json: () => Promise.resolve(f) }); }, MutationObserver: undefined, console };
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return { Q: win.SLQuarterly, fetched };
}
const { Q, fetched } = boot("#dashboard", {});
eq(fetched, [], "no request unless a stock page is open");
const un = (s) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

// no file / wrong file: an honest empty state, never numbers
for (const d of [null, undefined, {}, { kind: "other", stocks: {} }, { kind: "quarterly_results", stocks: { AAA: { symbol: "AAA", quarters: [] } } }, { kind: "quarterly_results", stocks: { ZZZ: DOCS.AAA.stocks.AAA } }]) {
  const h = Q.sectionHtml("AAA", d);
  re(un(h), /No quarterly results are on file for this company yet\./, "empty state");
  no(h, /<table/, "no table without data");
}

const py2 = "import sys,json; sys.path.insert(0,'.'); import test_quarterly_results as t, quarterly_results_updater as q\n" +
  "r=q.empty_record(); q.merge_quarters(r,q.parse_quarterly(t.REAL),'consolidated','2026-08-01'); r['last_checked']=r['last_success']='2026-08-01'\n" +
  "d,m,p=q.build_outputs({'AAA':t.ENTRIES['AAA']},{'AAA':r},'2026-08-01'); print(json.dumps(d['AAA']))";
const REALDOC = JSON.parse(cp.execFileSync("python3", ["-c", py2], { cwd: ROOT, encoding: "utf8" }));
// ---- the table: columns YoY | latest | previous quarter | same quarter last year; rows Revenue, Operating profit, Net profit, EPS
const cellsOf = (html, row) => { const m = html.match(new RegExp('<tr data-qr-row="' + row + '">([\\s\\S]*?)</tr>')); return m ? [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((x) => un(x[1])) : null; };
const mk = (qtrs, extra) => ({ schema_version: 1, kind: "quarterly_results", stocks: { AAA: Object.assign({ symbol: "AAA", basis: "consolidated", gaps: [], quarters: qtrs.map((x) => Object.assign({ quarter: "x", quarter_end: x[0], retrieved_on: "2026-10-14", values: Object.assign({ revenue: null, total_revenue: null, operating_profit: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, eps_diluted: null }, x[1]), growth: { yoy: { revenue: 999, profit_after_tax: 999 }, qoq: {} }, checks: x[2] || {}, revisions: [] })) }, extra || {}) } });
const V = (rev, op, pat, eps) => ({ revenue: rev, operating_profit: op, profit_after_tax: pat, eps_basic: eps });
const headers = (html) => [...html.match(/<thead>[\s\S]*?<\/thead>/)[0].matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((x) => un(x[1]));
const FULL = mk([["2025-09-30", V(100, 20, 10, 1.0)], ["2025-12-31", V(110, 21, 11, 1.1)], ["2026-03-31", V(120, 22, 12, 1.2)], ["2026-06-30", V(130, 26, 9, 0.9)], ["2026-09-30", V(150, 30, 12, 1.5)]]);

const t1 = Q.sectionHtml("AAA", FULL);
eq(headers(t1), ["", "YoY", "Sep 2026", "Jun 2026", "Sep 2025"], "columns: YoY, latest, previous quarter, same quarter last year");
re(un(t1), /Latest quarter: Sep 2026/, "latest quarter named");
eq([...t1.matchAll(/<tr data-qr-row="([a-z_]+)">/g)].map((x) => x[1]), ["revenue", "operating_profit", "profit_after_tax", "eps_basic"], "rows: Revenue, Operating profit, Net profit, EPS (no empty Total revenue row)");
eq(cellsOf(t1, "revenue"), ["Revenue", "▲ +50.00%", "150", "130", "100"], "revenue: YoY is latest vs four quarters earlier (150 vs 100), not vs the previous quarter (130)");
eq(cellsOf(t1, "operating_profit"), ["Operating profit", "▲ +50.00%", "30", "26", "20"], "operating profit row");
eq(cellsOf(t1, "profit_after_tax"), ["Net profit", "▲ +20.00%", "12", "9", "10"], "net profit YoY 12 vs 10");
eq(cellsOf(t1, "eps_basic"), ["EPS (₹)", "▲ +50.00%", "1.50", "0.90", "1.00"], "EPS shown with two decimals");
re(t1, /class="n qr-up">▲ \+50\.00%/, "positive growth is green (qr-up) and has an arrow, not colour alone");
no(t1, /999/, "the stored growth field is never displayed; YoY is calculated from the stored values");
no(t1, /<t[hd][^>]*>\s*EBITDA/i, "no EBITDA row or column");
no(t1, /data-qr="why"/, "no explanation note when every YoY is available");

// negative growth is red with a down arrow; zero growth is neutral
const dn = Q.sectionHtml("AAA", mk([["2025-09-30", V(200, 20, 10, 2)], ["2026-09-30", V(150, 20, 5, 1)]]));
re(dn, /class="n qr-dn">▼ -25\.00%/, "decline: red, down arrow, minus sign"); re(dn, /class="n qr-dn">▼ -50\.00%/, "net profit decline");
re(dn, /class="n qr-na">0\.00%/, "zero growth is neutral (operating profit 20 vs 20)");

// the year-ago quarter is missing (the real TCS situation: four stored quarters): YoY is a dash with a clear reason, never 0 and never QoQ
const four = Q.sectionHtml("AAA", mk([["2025-12-31", V(110, 21, 11, null)], ["2026-03-31", V(120, 22, 12, null)], ["2026-06-30", V(130, 26, 9, null)], ["2026-09-30", V(150, 30, 12, null)]]));
eq(headers(four), ["", "YoY", "Sep 2026", "Jun 2026", "Sep 2025"], "the year-ago column is still labelled");
eq(cellsOf(four, "revenue"), ["Revenue", "—", "150", "130", "—"], "no year-ago quarter: YoY and that column are dashes");
eq(cellsOf(four, "profit_after_tax").slice(1, 2), ["—"], "net profit YoY is a dash too");
re(un(four), /the year-ago quarter \(Sep 2025\) is not in the data - the source returned only 4 quarters \(Dec 2025 to Sep 2026\)/, "the explanation names the missing quarter and what the source returned");
eq(cellsOf(four, "eps_basic"), ["EPS (₹)", "—", "—", "—", "—"], "EPS unavailable: dashes, never 0");
no(four, /qr-up|qr-dn/, "no green or red without a real growth figure");

// quarters are matched by DATE, not by position: a missing previous quarter and a shuffled file
const shuffled = mk([["2026-09-30", V(150, 30, 12, 1.5)], ["2025-09-30", V(100, 20, 10, 1.0)], ["2026-03-31", V(120, 22, 12, 1.2)]]);   // no Jun 2026; newest first/last mixed
const sh = Q.sectionHtml("AAA", shuffled);
eq(cellsOf(sh, "revenue"), ["Revenue", "▲ +50.00%", "150", "—", "100"], "previous quarter missing -> dash there; YoY still uses Sep 2025 (not the adjacent stored quarter 120)");
const lone = Q.sectionHtml("AAA", mk([["2026-09-30", V(150, 30, 12, 1.5)]]));
eq(cellsOf(lone, "revenue"), ["Revenue", "—", "150", "—", "—"], "a single quarter");
const notLatest = Q.sectionHtml("AAA", mk([["2026-06-30", V(130, 26, 9, null)], ["2025-12-31", V(110, 21, 11, null)]]));
eq(cellsOf(notLatest, "revenue")[2], "130", "the latest quarter is the newest by date");

// missing values and bad bases
const miss = Q.sectionHtml("AAA", mk([["2025-09-30", V(100, null, 10, 1)], ["2026-09-30", V(null, 30, null, 1.5)]]));
eq(cellsOf(miss, "revenue"), ["Revenue", "—", "—", "—", "100"], "latest revenue missing: YoY dash, never 0");
eq(cellsOf(miss, "profit_after_tax").slice(1), ["—", "—", "—", "10"], "latest net profit missing");
eq(cellsOf(miss, "operating_profit").slice(1, 2), ["—"], "year-ago operating profit missing");
re(un(miss), /a value is unavailable/, "reason: a value is unavailable");
const zero = Q.sectionHtml("AAA", mk([["2025-09-30", V(0, 0, -5, 0)], ["2026-09-30", V(150, 30, 12, 1.5)]]));
eq(cellsOf(zero, "revenue").slice(1, 2), ["—"], "zero base: no division, dash"); eq(cellsOf(zero, "profit_after_tax").slice(1, 2), ["—"], "negative base: dash, not a misleading percentage");
re(un(zero), /year-ago base is zero or negative/, "reason: base is zero or negative"); no(zero, /Infinity|NaN/, "no Infinity or NaN");
eq(cellsOf(zero, "revenue")[4], "0", "a real zero value stays 0");
const bad = Q.sectionHtml("AAA", mk([["2025-09-30", V("100", NaN, Infinity, 1)], ["2026-09-30", V("150", 30, 12, 1.5)]]));
eq(cellsOf(bad, "revenue").slice(1, 2), ["—"], "a string value is not a number"); no(bad, /NaN|Infinity/, "NaN/Infinity are not shown");
const loss = Q.sectionHtml("AAA", mk([["2025-09-30", V(100, 20, 10, 1)], ["2026-09-30", V(150, 30, -4, -0.5)]]));
eq(cellsOf(loss, "profit_after_tax").slice(1, 3), ["▼ -140.00%", "-4"], "a loss against a positive base is a real decline");

// Total revenue (banks) only when some shown quarter has it; operating profit that equals PBT is flagged
const bank = Q.sectionHtml("AAA", mk([["2025-09-30", { total_revenue: 80 }], ["2026-09-30", { total_revenue: 100, revenue: 90 }, { operating_profit_equals_pbt: true }]]));
eq(cellsOf(bank, "total_revenue"), ["Total revenue", "▲ +25.00%", "100", "—", "80"], "Total revenue row appears when present");
re(un(bank), /operating profit equals its profit before tax/, "operating profit = PBT is stated");

// matching helpers
eq([Q.shift("2026-09-30", -12), Q.shift("2026-09-30", -3), Q.shift("2026-03-31", -1), Q.shift("2024-03-31", -1), Q.shift("2026-01-31", -3), Q.shift("2025-12-31", 3), Q.shift("junk", -3), Q.shift("2026-13-01", -3)],
   ["2025-09-30", "2026-06-30", "2026-02-28", "2024-02-29", "2025-10-31", "2026-03-31", null, null], "shift: month-end arithmetic incl. leap years; invalid dates give null");
eq([Q.growth(150, 100, true).pct, Q.growth(150, 0, true).pct, Q.growth(150, -1, true).pct, Q.growth(null, 100, true).pct, Q.growth(150, null, true).pct, Q.growth(150, 100, false).pct, Q.growth(100, 100, true).pct, Q.growth(1, 3, true).pct],
   [50, null, null, null, null, null, 0, -66.67], "growth: positive base only, two real numbers, year-ago quarter present; rounded to 2 dp");
eq(Q.growth(150, 100, false).why, "year-ago quarter not in the data", "growth: reason");

// the real updater output (JSON built by quarterly_results_updater from the real response shape) renders and matches
const hr = Q.sectionHtml("AAA", REALDOC);
re(un(hr), /Latest quarter: Jun 2026/, "real-shape: latest quarter"); eq(cellsOf(hr, "revenue").slice(1, 5), ["▲ +50.00%", "1,500", "1,300", "1,000"], "real-shape: revenue YoY from the stored quarters (1500 vs 1000)");
eq(cellsOf(hr, "profit_after_tax").slice(1, 5), ["▲ +50.00%", "150", "130", "100"], "real-shape: net profit YoY (150 vs 100)");
eq(cellsOf(hr, "operating_profit").slice(1, 5), ["▲ +50.00%", "300", "240", "200"], "real-shape: operating profit"); eq(cellsOf(hr, "eps_basic"), ["EPS (₹)", "—", "—", "—", "—"], "real-shape: EPS is not in the source -> dashes");
no(hr, /Total revenue/, "no all-dash Total revenue row"); no(un(hr).replace(/EBITDA is not provided/, ""), /EBITDA/, "EBITDA is never a label");
re(un(Q.sectionHtml("AAA", DOCS.AAA)), /Latest quarter: Jun 2026/, "the updater's fixture document renders");
no(un(hr).replace(/No rating, target or advice\./, ""), /\b(buy|sell|hold|bullish|bearish|target|rating|recommend\w*|outperform\w*)\b/i, "no advice wording");
re(un(hr), /not live market data/i, "not-live note"); re(hr, /<div class="scroll"><table/, "the table sits in a horizontally scrollable wrapper (responsive)");

// gaps are still listed; html in data is escaped
const gp = Q.sectionHtml("AAA", mk([["2025-09-30", V(100, 1, 1, 1)], ["2026-09-30", V(150, 1, 1, 1)]], { gaps: [{ between: ["2025-09-30", "2026-03-31"] }] }));
re(un(gp), /Quarters missing from the data \(not zero\): between Sep 2025 and Mar 2026/, "a gap is listed, not filled");
const doc3 = JSON.parse(JSON.stringify(DOCS.AAA)); doc3.stocks.AAA.basis = "<img src=x onerror=alert(1)>";
no(Q.sectionHtml("AAA", doc3), /<img/, "data is escaped");
// formatting helpers
eq([Q.num(null), Q.num("5"), Q.num(0), Q.num(1234.5)], ["-", "-", "0", "1,234.5"], "num: only real numbers, a real zero stays 0");
eq([Q.pct(null), Q.pct(0), Q.pct(-3.456), Q.pct(2)], ["-", "0.00%", "-3.46%", "+2.00%"], "pct: null is -");

// the request: one file per stock, only for a stock page
const files = { "out/by_symbol/quarterly_results/AAA.json": DOCS.AAA };
const b = boot("#stock=AAA", files); b.Q.check();
eq(b.fetched.length, 0, "check() places nothing and requests nothing when the detail view is not rendered");
console.log("Quarterly Results UI tests passed (" + checks + " checks)");
