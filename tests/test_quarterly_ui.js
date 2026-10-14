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
// ---- the table: the latest four quarters as columns (newest first), exactly three rows, one QoQ Growth column
const cellsOf = (html, row) => { const m = html.match(new RegExp('<tr data-qr-row="' + row + '">([\\s\\S]*?)</tr>')); return m ? [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((x) => un(x[1])) : null; };
const mk = (qtrs, extra) => ({ schema_version: 1, kind: "quarterly_results", stocks: { AAA: Object.assign({ symbol: "AAA", basis: "consolidated", gaps: [], quarters: qtrs.map((x) => Object.assign({ quarter: "x", quarter_end: x[0], retrieved_on: "2026-10-14", values: Object.assign({ revenue: null, total_revenue: null, operating_profit: null, profit_before_tax: null, profit_after_tax: null, eps_basic: null, eps_diluted: null }, x[1]), growth: { yoy: { revenue: 999, profit_after_tax: 999 }, qoq: { revenue: 888, profit_after_tax: 888 } }, checks: x[2] || {}, revisions: [] })) }, extra || {}) } });
const V = (rev, op, pat) => ({ revenue: rev, operating_profit: op, profit_after_tax: pat });
const headers = (html) => [...html.match(/<thead>[\s\S]*?<\/thead>/)[0].matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((x) => un(x[1]));
const rowsOf = (html) => [...html.matchAll(/<tr data-qr-row="([a-z_]+)">/g)].map((x) => x[1]);
const FIVE = mk([["2025-09-30", V(100, 20, 10)], ["2025-12-31", V(110, 21, 11)], ["2026-03-31", V(120, 22, 12)], ["2026-06-30", V(130, 26, 9)], ["2026-09-30", V(150, 30, 12)]]);

const t1 = Q.sectionHtml("AAA", FIVE);
eq(headers(t1), ["", "Sep 2026", "Jun 2026", "Mar 2026", "Dec 2025", "QoQ Growth"], "the latest four quarters, newest first, then one QoQ Growth column (Sep 2025 is the fifth and is not shown)");
eq(rowsOf(t1), ["revenue", "operating_profit", "profit_after_tax"], "exactly three rows");
eq([...t1.matchAll(/<tr data-qr-row="[a-z_]+"><td>([^<]*)<\/td>/g)].map((x) => x[1]), ["Revenue", "Operating Profit", "Net Profit"], "row labels");
re(un(t1), /Latest quarter: Sep 2026 \(consolidated; ₹ crore\)/, "values are in ₹ crore");
eq(cellsOf(t1, "revenue"), ["Revenue", "150", "130", "120", "110", "+15.38%"], "revenue QoQ = (150-130)/130 = +15.38%");
eq(cellsOf(t1, "operating_profit"), ["Operating Profit", "30", "26", "22", "21", "+15.38%"], "operating profit QoQ = (30-26)/26");
eq(cellsOf(t1, "profit_after_tax"), ["Net Profit", "12", "9", "12", "11", "+33.33%"], "net profit QoQ = (12-9)/9");
re(t1, /class="n qr-up">\+15\.38%/, "positive growth is green with a plus sign");
no(t1, /888|999/, "stored growth fields are never displayed: QoQ is calculated from the stored values");
no(t1.match(/<table[\s\S]*<\/table>/)[0], /YoY|EPS|Stored|Total revenue/, "no YoY, EPS, Stored or Total revenue in the table");
no(t1.match(/<table[\s\S]*<\/table>/)[0], /EBITDA/i, "Operating Profit is never labelled EBITDA");
no(t1, /data-qr="why"/, "no explanation note when every QoQ is available");
re(un(t1), /QoQ Growth compares the latest quarter with the immediately preceding quarter/, "the note defines QoQ");
eq((t1.match(/<th[\s>]/g) || []).length, 6, "six header cells: metric, four quarters, QoQ Growth");

// negative growth is red with a minus sign; zero is neutral
const dn = Q.sectionHtml("AAA", mk([["2026-06-30", V(200, 20, 10)], ["2026-09-30", V(150, 20, 5)]]));
re(dn, /class="n qr-dn">−25\.00%/, "revenue decline: red, true minus sign"); re(dn, /class="n qr-dn">−50\.00%/, "net profit decline");
re(dn, /class="n qr-na">0\.00%/, "zero growth is neutral (operating profit 20 vs 20)");
no(dn.match(/<tbody>[\s\S]*<\/tbody>/)[0], /[▲▼]/, "no arrows: sign and colour only");

// quarters shown: fewer than four stored -> only those; more than four -> latest four; order of the file does not matter
eq(headers(Q.sectionHtml("AAA", mk([["2026-09-30", V(150, 30, 12)], ["2026-06-30", V(130, 26, 9)]]))), ["", "Sep 2026", "Jun 2026", "QoQ Growth"], "two stored quarters: two columns, not padded");
eq(headers(Q.sectionHtml("AAA", mk([["2026-09-30", V(150, 30, 12)], ["2025-12-31", V(1, 1, 1)], ["2026-03-31", V(120, 22, 12)], ["2026-06-30", V(130, 26, 9)], ["2025-09-30", V(1, 1, 1)]]))), ["", "Sep 2026", "Jun 2026", "Mar 2026", "Dec 2025", "QoQ Growth"], "a shuffled file is ordered by date");

// the immediately preceding quarter is matched by DATE: a missing Jun 2026 gives a dash, never growth against Mar 2026
const gapq = Q.sectionHtml("AAA", mk([["2026-09-30", V(150, 30, 12)], ["2026-03-31", V(120, 22, 12)], ["2025-12-31", V(110, 21, 11)]], { gaps: [{ between: ["2026-03-31", "2026-09-30"] }] }));
eq(cellsOf(gapq, "revenue"), ["Revenue", "150", "120", "110", "—"], "previous quarter missing: QoQ is a dash (not 150 vs 120)");
re(un(gapq), /the previous quarter \(Jun 2026\) is not in the data/, "reason names the missing quarter");
re(un(gapq), /Quarters missing from the data \(not zero\): between Mar 2026 and Sep 2026/, "the gaps message is kept");
eq(cellsOf(Q.sectionHtml("AAA", mk([["2026-09-30", V(150, 30, 12)]])), "revenue"), ["Revenue", "150", "—"], "a single quarter: QoQ is a dash");

// missing / invalid values and bad denominators
const miss = Q.sectionHtml("AAA", mk([["2026-06-30", V(100, null, 10)], ["2026-09-30", V(null, 30, null)]]));
eq(cellsOf(miss, "revenue"), ["Revenue", "—", "100", "—"], "latest revenue missing: dash, never 0");
eq(cellsOf(miss, "operating_profit"), ["Operating Profit", "30", "—", "—"], "previous operating profit missing: dash");
eq(cellsOf(miss, "profit_after_tax"), ["Net Profit", "—", "10", "—"], "latest net profit missing: dash");
re(un(miss), /a value is unavailable/, "reason: a value is unavailable");
const zero = Q.sectionHtml("AAA", mk([["2026-06-30", V(0, 0, -5)], ["2026-09-30", V(150, 30, 12)]]));
eq(cellsOf(zero, "revenue"), ["Revenue", "150", "0", "—"], "zero denominator: no division, dash; the real zero value stays 0");
eq(cellsOf(zero, "profit_after_tax"), ["Net Profit", "12", "-5", "—"], "negative denominator: dash, not a misleading sign");
re(un(zero), /previous quarter is zero/, "reason: zero"); re(un(zero), /previous quarter is negative/, "reason: negative"); no(zero, /Infinity|NaN/, "no Infinity or NaN");
const bad = Q.sectionHtml("AAA", mk([["2026-06-30", V("100", NaN, Infinity)], ["2026-09-30", V("150", 30, 12)]]));
eq([cellsOf(bad, "revenue")[3], cellsOf(bad, "operating_profit")[3], cellsOf(bad, "profit_after_tax")[3]], ["—", "—", "—"], "strings, NaN and Infinity are invalid: dashes"); no(bad, /NaN|Infinity/, "NaN/Infinity are not shown");
const loss = Q.sectionHtml("AAA", mk([["2026-06-30", V(100, 20, 10)], ["2026-09-30", V(150, 30, -4)]]));
eq(cellsOf(loss, "profit_after_tax").slice(1), ["-4", "10", "−140.00%"], "a loss against a positive base is a real decline");
eq(cellsOf(Q.sectionHtml("AAA", mk([["2026-06-30", V(3, 3, 3)], ["2026-09-30", V(1, 1, 1)]])), "revenue")[3], "−66.67%", "rounded to two decimals");

// operating profit equal to profit before tax is stated; banks' Total revenue is not a row
const bank = Q.sectionHtml("AAA", mk([["2026-06-30", { total_revenue: 80, revenue: 70 }], ["2026-09-30", { total_revenue: 100, revenue: 90 }, { operating_profit_equals_pbt: true }]]));
re(un(bank), /operating profit equals its profit before tax/, "operating profit = PBT is stated"); eq(rowsOf(bank), ["revenue", "operating_profit", "profit_after_tax"], "still exactly three rows");

// helpers
eq([Q.shift("2026-09-30", -3), Q.shift("2026-03-31", -1), Q.shift("2024-03-31", -1), Q.shift("2026-01-31", -3), Q.shift("2025-12-31", 3), Q.shift("junk", -3), Q.shift("2026-13-01", -3)],
   ["2026-06-30", "2026-02-28", "2024-02-29", "2025-10-31", "2026-03-31", null, null], "shift: month-end arithmetic incl. leap years; invalid dates give null");
eq([Q.qoq(150, 100, true).pct, Q.qoq(150, 0, true).pct, Q.qoq(150, -1, true).pct, Q.qoq(null, 100, true).pct, Q.qoq(150, null, true).pct, Q.qoq(150, 100, false).pct, Q.qoq(100, 100, true).pct, Q.qoq(1, 3, true).pct, Q.qoq(0, 100, true).pct],
   [50, null, null, null, null, null, 0, -66.67, -100], "qoq: positive base, two real numbers, previous quarter present; a real zero latest value is -100%");
eq([Q.qoq(150, 100, false).why, Q.qoq(1, 0, true).why, Q.qoq(1, -1, true).why, Q.qoq("1", 1, true).why], ["previous quarter not in the data", "previous quarter is zero", "previous quarter is negative", "a value is unavailable"], "qoq: reasons");

// the real updater output (built by quarterly_results_updater from the real response shape)
const hr = Q.sectionHtml("AAA", REALDOC);
re(un(hr), /Latest quarter: Jun 2026/, "real-shape: latest quarter");
eq(headers(hr), ["", "Jun 2026", "Mar 2026", "Dec 2025", "Sep 2025", "QoQ Growth"], "real-shape: the latest four of five stored quarters");
eq(cellsOf(hr, "revenue"), ["Revenue", "1,500", "1,300", "1,200", "1,100", "+15.38%"], "real-shape: revenue QoQ from stored quarters (1500 vs 1300)");
eq(cellsOf(hr, "profit_after_tax"), ["Net Profit", "150", "130", "120", "110", "+15.38%"], "real-shape: net profit");
eq(cellsOf(hr, "operating_profit"), ["Operating Profit", "300", "240", "230", "210", "+25.00%"], "real-shape: operating profit (300 vs 240)");
no(hr, /EPS|Total revenue/, "real-shape: no EPS or Total revenue");
re(un(Q.sectionHtml("AAA", DOCS.AAA)), /Latest quarter: Jun 2026/, "the updater's fixture document renders");
no(un(hr).replace(/No rating, target or advice\./, ""), /\b(buy|sell|hold|bullish|bearish|target|rating|recommend\w*|outperform\w*)\b/i, "no advice wording");
re(un(hr), /not live market data/i, "not-live note"); re(hr, /<div class="scroll"><table/, "horizontally scrollable wrapper (mobile)");
const doc3 = JSON.parse(JSON.stringify(DOCS.AAA)); doc3.stocks.AAA.basis = "<img src=x onerror=alert(1)>";
no(Q.sectionHtml("AAA", doc3), /<img/, "data is escaped");
eq([Q.num(null), Q.num("5"), Q.num(0), Q.num(1234.5)], ["-", "-", "0", "1,234.5"], "num: only real numbers, a real zero stays 0");
eq([Q.pct(null), Q.pct(0), Q.pct(-3.456), Q.pct(2)], ["-", "0.00%", "-3.46%", "+2.00%"], "pct: null is -");

// the request: one file per stock, only for a stock page
const files = { "out/by_symbol/quarterly_results/AAA.json": DOCS.AAA };
const b = boot("#stock=AAA", files); b.Q.check();
eq(b.fetched.length, 0, "check() places nothing and requests nothing when the detail view is not rendered");
console.log("Quarterly Results UI tests passed (" + checks + " checks)");
