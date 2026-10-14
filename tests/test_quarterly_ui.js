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

// the real document
const h = Q.sectionHtml("AAA", DOCS.AAA), t = un(h);
re(t, /Latest quarter: Jun 2026/, "latest quarter named");
eq((h.match(/<tbody>[\s\S]*<\/tbody>/)[0].match(/<tr>/g) || []).length, DOCS.AAA.stocks.AAA.quarters.length, "one row per stored quarter");
re(h, /\+50\.00%/, "stored YoY growth shown as stored (50.0)"); re(h, /\+15\.38%/, "stored QoQ growth shown as stored (15.38)");
re(t, /not the date the company declared it/, "retrieval date is not passed off as a declaration date");
no(t, /EBITDA is calculated|operating profit/i, "no operating profit or EBITDA figure");
no(h, /Operating/i, "no operating-profit column");
no(t.replace(/No rating, target or advice\./, ""), /\b(buy|sell|hold|bullish|bearish|target|rating|recommend\w*|outperform\w*)\b/i, "no advice wording");

// EPS is stored by the updater but is not part of the first displayed version
no(h, /EPS/i, "no EPS column or wording");
// the REAL response shape (full_statement null, summary categories only): Revenue and Net profit shown, growth from stored quarters, no empty Total revenue column
const py2 = "import sys,json; sys.path.insert(0,'.'); import test_quarterly_results as t, quarterly_results_updater as q\n" +
  "r=q.empty_record(); q.merge_quarters(r,q.parse_quarterly(t.REAL),'consolidated','2026-08-01'); r['last_checked']=r['last_success']='2026-08-01'\n" +
  "d,m,p=q.build_outputs({'AAA':t.ENTRIES['AAA']},{'AAA':r},'2026-08-01'); print(json.dumps(d['AAA']))";
const REALDOC = JSON.parse(cp.execFileSync("python3", ["-c", py2], { cwd: ROOT, encoding: "utf8" }));
const hr = Q.sectionHtml("AAA", REALDOC), tr = un(hr);
re(tr, /Latest quarter: Jun 2026/, "real-shape: latest quarter"); re(hr, /1,500/, "real-shape: revenue shown"); re(hr, /<td class="n">150<\/td>/, "real-shape: net profit shown");
re(hr, /\+50\.00%/, "real-shape: YoY from stored quarters"); re(hr, /\+15\.38%/, "real-shape: QoQ from stored quarters");
no(hr, /Total revenue/, "no all-dash Total revenue column when no quarter has it"); no(hr.match(/<table[\s\S]*<\/table>/)[0], /EPS|EBITDA|Operating/i, "no EPS, EBITDA or operating-profit column in the table");
const lastRow = hr.match(/<tbody>[\s\S]*?<\/tbody>/)[0].match(/<tr>[\s\S]*?<\/tr>/g).pop();
eq((lastRow.match(/<td class="n">-<\/td>/g) || []).length, 4, "the oldest quarter has no comparison quarters: all four growth cells are dashes");
no(lastRow, /<td class="n">0(\.00%)?<\/td>/, "and none of them is 0");

// a null value is "-", never 0; a gap is named; nothing is carried forward
const doc2 = JSON.parse(JSON.stringify(DOCS.AAA)); const s2 = doc2.stocks.AAA;
s2.quarters[0].values.revenue = null; s2.quarters[0].growth.yoy.revenue = null; s2.quarters[1].values.profit_after_tax = null;
s2.gaps = [{ between: ["2025-09-30", "2026-03-31"] }];
const h2 = Q.sectionHtml("AAA", doc2), row0 = h2.match(/<tbody><tr>([\s\S]*?)<\/tr>/)[1];
re(row0, /<td class="n">-<\/td>/, "a null revenue shows as -"); no(row0.split("</td>")[1] || "", /^<td class="n">0/, "never shown as 0");
re(un(h2), /Quarters missing from the data \(not zero\): between Sep 2025 and Mar 2026/, "a gap is listed, not filled");
// html in data is escaped
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
