// Run: node tests/test_corporate_actions_ui.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the read-only Dividends & Corporate Actions section. Its input is written by the real fundamentals_extra_updater (through
// test_fundamentals_extra's mock bodies, whose values are invented), so the page is tested against the shape the updater really writes.
const fs = require("fs"), assert = require("assert"), cp = require("child_process"), vm = require("vm");
const ROOT = __dirname + "/..";
const html = fs.readFileSync(ROOT + "/index.html", "utf8");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); }, re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };
const TAG = '<script type="module" id="stocklens-corporate-actions">';
eq(html.split(TAG).length, 2, "exactly one Corporate Actions module");
const code = html.split(TAG)[1].split("</script>")[0];

// documents written by the real updater: AAA with events, BBB with none on record, CCC whose amount is missing
const py = [
  "import sys,json,tempfile,pathlib; sys.path.insert(0,'.'); import test_fundamentals_extra as t, fundamentals_extra_updater as fx",
  "R=t.RunAndState().responses(); R['INE000C01017/corporate-actions']={'data':[{'name':'Dividend','expiry_date':'01 Oct 2026','amount':'-'}]}",
  "c=t.new_cache(); fx.update(t.FakeApi(R),['AAA','BBB','CCC'],t.INSTR,c,['corporate_actions'],t.TODAY)",
  "d=tempfile.mkdtemp(); fx.write_outputs(d,['AAA','BBB','CCC'],c,['corporate_actions'],t.TODAY)",
  "print(json.dumps({s: json.loads((pathlib.Path(d)/'out/by_symbol/corporate_actions'/(s+'.json')).read_text()) for s in ('AAA','BBB','CCC')}))"].join("\n");
const DOCS = JSON.parse(cp.execFileSync("python3", ["-c", py], { cwd: ROOT, encoding: "utf8" }));
ok(DOCS.AAA && DOCS.BBB && DOCS.CCC, "the updater produced the files");

function boot(hash, files) {
  const els = {}, fetched = [];
  const win = { location: { hash }, SLCorporateActions: null };
  const ctx = { window: win, document: { getElementById: (i) => els[i] || null, head: null, createElement: () => ({}) },
    fetch: (u) => { fetched.push(u); const f = files[u]; return Promise.resolve(f === undefined ? { ok: false } : { ok: true, json: () => Promise.resolve(f) }); }, MutationObserver: undefined, console };
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return { C: win.SLCorporateActions, fetched };
}
const { C, fetched } = boot("#dashboard", {});
eq(fetched, [], "no request unless a stock page is open");
const un = (s) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const prices = (sym, close, date) => ({ stocks: { [sym]: { candles: [{ date: "2026-10-26", close: 1 }, { date: date || "2026-10-27", close }] } } });

// no file, or a file for another stock: "not fetched", never numbers
for (const d of [null, undefined, {}, { kind: "share_holdings", stocks: {} }, DOCS.BBB]) {
  const h = C.sectionHtml("AAA", d, prices("AAA", 250));
  re(un(h), /Corporate actions have not been fetched for this company yet\./, "empty state");
  no(h, /<table|ca-card/, "no table or figures without a file");
}

// AAA: 12-month dividend 5.5 + 2 = 7.5; yield on a close of 250 = 3.00%
let h = C.sectionHtml("AAA", DOCS.AAA, prices("AAA", 250));
re(h, /data-ca="ttm">₹7\.50</, "12-month dividend from the updater");
re(h, /data-ca="yield">3\.00%</, "yield = 7.5 / 250");
re(un(h), /close of 27 Oct 2026 \(₹250\.00\), the last close on file, not a live price/, "the close used is named");
eq((h.match(/<tr data-ca-type/g) || []).length, 5, "every event listed");
re(un(h), /14 Aug 2026 Dividend ₹5\.50 \(Final\) 14 Aug 2026/, "dividend row");
re(un(h), /28 Oct 2024 Bonus 1:1 —/, "bonus row: ratio, unknown record date is a dash");
re(un(h), /2 Jan 2023 Split 10 : 2/, "split row");
re(un(h), /Fetched 28 Oct 2026/, "fetch date shown");

// yield withheld, each with its reason, never 0
re(un(C.sectionHtml("AAA", DOCS.AAA, null)), /— means unavailable: no closing price is on file/, "no price");
re(C.sectionHtml("AAA", DOCS.AAA, null), /data-ca="yield">—</, "no yield without a price");
re(un(C.sectionHtml("AAA", DOCS.AAA, prices("AAA", 0))), /the last close is not above zero/, "zero close");
const split = JSON.parse(JSON.stringify(DOCS.AAA));
split.stocks.AAA.events.unshift({ type: "split", name: "Stock Split", ex_date: "2026-09-01", ratio: "10:1", amount: null, record_date: null });
re(un(C.sectionHtml("AAA", split, prices("AAA", 250))), /a split or bonus went ex in the same 12 months/, "mixed per-share bases");
const c = un(C.sectionHtml("CCC", DOCS.CCC, prices("CCC", 100)));
re(c, /a dividend in the last 12 months has no amount in the source/, "missing amount: no sum");
re(c, /1 Oct 2026 Dividend — /, "missing amount shows a dash");
no(C.sectionHtml("CCC", DOCS.CCC, prices("CCC", 100)), /₹0\.00/, "never zero");

// BBB: a valid empty answer is said as such
const b = un(C.sectionHtml("BBB", DOCS.BBB, prices("BBB", 100)));
re(b, /No dividend, bonus, split or rights issue is on record in the source for this company\./, "none on record");
re(b, /no dividend went ex in the last 12 months/, "why no yield");

// a later failed refresh keeps the saved actions and says so
const stale = JSON.parse(JSON.stringify(DOCS.AAA)); stale.stocks.AAA.status = "failed";
re(un(C.sectionHtml("AAA", stale, prices("AAA", 250))), /The latest refresh did not succeed, so these are the actions saved earlier\. Fetched 28 Oct 2026/, "stale");

// escaping: text from the source is never HTML
const evil = JSON.parse(JSON.stringify(DOCS.AAA)); evil.stocks.AAA.events[0].dividend_type = "<img src=x>";
no(C.sectionHtml("AAA", evil, prices("AAA", 250)), /<img/, "escaped");

// last close: newest valid candle only
eq(JSON.parse(JSON.stringify(C.lastClose({ stocks: { X: { candles: [{ date: "2026-10-27", close: 5 }, { date: "bad", close: 9 }, { date: "2026-10-28", close: null }, { date: "2026-10-20", close: 4 }] } } }, "X"))), { close: 5, date: "2026-10-27" }, "lastClose");

// mounting on a stock page: one request for the stock's file and one for prices, placed after Shareholding
(function () {
  const kids = [];
  const box = { innerHTML: "", querySelector: () => null, insertBefore: (el, ref) => { el.parentNode = box; kids.push([el.id, ref && ref.id]); } };
  const sh = { id: "detailShareholding", parentNode: box, nextSibling: { id: "next" } };
  const fetched = [], win = { location: { hash: "#stock=AAA" } };
  const files = { "out/by_symbol/corporate_actions/AAA.json": DOCS.AAA, "out/historical.json": prices("AAA", 250) };
  const els = { detail: box, detailShareholding: sh };
  const ctx = { window: win, document: { getElementById: (i) => els[i] || null, head: null, createElement: () => ({}) },
    fetch: (u) => { fetched.push(u); const f = files[u]; return Promise.resolve(f === undefined ? { ok: false } : { ok: true, json: () => Promise.resolve(f) }); }, MutationObserver: undefined, console };
  vm.createContext(ctx); vm.runInContext(code, ctx);
  eq(kids, [["detailCorporateActions", "next"]], "placed directly after the Shareholding Pattern section");
  eq(fetched, ["out/by_symbol/corporate_actions/AAA.json", "out/historical.json"], "two reads, no other request");
})();

console.log("Corporate actions UI tests passed (" + checks + " checks)");
