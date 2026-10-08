// Run: node tests/test_shards_loader.js   (no network, no browser; stubs window, fetch and location)
// Tests the per-stock-file layer (<script id="stocklens-shards">): the three single files stay exactly as they were for the home page and for the
// 10 development stocks; a stock the single file does not hold gets its own file added to the same document shape; a missing file leaves the stock absent;
// the page reloads once (never in a loop) when its in-memory documents cannot serve the new route.
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const m = /<script id="stocklens-shards">([\s\S]*?)<\/script>/.exec(html);
let checks = 0;
const norm = (x) => (x === undefined ? x : JSON.parse(JSON.stringify(x))), eq = (a, b, msg) => { checks++; assert.deepStrictEqual(norm(a), norm(b), msg); }, ok = (c, msg) => { checks++; assert.ok(c, msg); };
ok(m && m[1].length > 500, "the layer is in the page");
ok(html.split('<script id="stocklens-shards">').length === 2, "exactly one copy");
ok(html.indexOf('<script id="stocklens-shards">') < html.indexOf("<script>"), "it runs before the other scripts");

const MONO = {
  historical: { updated: "d", interval: "1day", benchmark: { candles: [1] }, stocks: { TCS: { symbol: "TCS", candles: [1] } } },
  financial_history: { schema: 1, stocks: { TCS: { symbol: "TCS", years: [] } } },
  shareholding: { schema: 1, stocks: { TCS: { symbol: "TCS", quarters: [] } } },
};
function world(opts) {
  opts = opts || {};
  const calls = [], listeners = {}, state = { reloads: 0 };
  const files = opts.files || {};
  const win = {
    location: { hash: opts.hash || "", reload() { state.reloads++; } },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    fetch(u) {
      calls.push(u);
      const hit = files[u];
      if (hit === undefined) return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error("no")) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(hit))) });
    },
  };
  win.window = win;
  vm.runInNewContext(m[1], win);
  return { win, calls, state, go(h) { win.location.hash = h; (listeners.hashchange || []).forEach((f) => f()); } };
}
const shard = (kind, sym, extra) => ({ [`out/by_symbol/${kind}/${sym}.json`]: { schema: 1, as_of: "d", stocks: { [sym]: Object.assign({ symbol: sym }, extra || {}) } } });
const mono = (kind) => ({ [`out/${kind === "historical" ? "historical" : kind}.json`]: MONO[kind] });
const files = (...parts) => Object.assign({}, ...parts);
const get = async (w, kind) => { const r = await w.win.fetch(`out/${kind}.json`, { cache: "no-store" }); return r.ok ? r.json() : null; };

(async () => {
  // 1. home page and unrelated URLs: untouched, one request
  let w = world({ hash: "", files: files(mono("historical")) });
  eq((await get(w, "historical")).stocks.TCS.symbol, "TCS"); eq(w.calls, ["out/historical.json"], "home page: only the single file");
  await w.win.fetch("out/fundamentals.json"); eq(w.calls.length, 2); eq(w.calls[1], "out/fundamentals.json", "other URLs pass straight through");

  // 2. a development stock: no extra request, same content
  w = world({ hash: "#stock=TCS", files: files(mono("historical"), mono("financial_history"), mono("shareholding")) });
  for (const k of ["historical", "financial_history", "shareholding"]) eq((await get(w, k)).stocks, MONO[k].stocks, k + " as before");
  eq(w.calls.length, 3, "no per-stock request for a stock the single file holds"); eq(w.state.reloads, 0);

  // 3. a stock outside the single file: its own file is added, the document keeps its shape
  w = world({ hash: "#stock=ALPHA", files: files(mono("historical"), shard("historical", "ALPHA", { candles: [2] })) });
  let d = await get(w, "historical");
  eq(Object.keys(d.stocks).sort(), ["ALPHA", "TCS"]); eq(d.stocks.ALPHA.candles, [2]); eq(d.benchmark, MONO.historical.benchmark, "benchmark kept"); eq(d.interval, "1day");
  eq(w.calls, ["out/historical.json", "out/by_symbol/historical/ALPHA.json"], "one single file + one stock file");
  ok(w.win.SLShards.state().polluted, "remembered that a stock file was added");

  // 4. no file for the stock: nothing is invented, and no reload loop later
  w = world({ hash: "#stock=NOFILE", files: files(mono("shareholding")) });
  d = await get(w, "shareholding"); eq(Object.keys(d.stocks), ["TCS"]); eq(w.win.SLShards.state().polluted, false);
  w.go("#stock=NOFILE"); eq(w.state.reloads, 0, "a stock known to have no file does not reload again");

  // 5. the single file is missing but the stock has its own file
  w = world({ hash: "#stock=ALPHA", files: files(shard("financial_history", "ALPHA", { years: [] })) });
  d = await get(w, "financial_history"); eq(Object.keys(d.stocks), ["ALPHA"]); eq(d.schema, 1);
  // ... and with neither, the original failed response comes back
  w = world({ hash: "#stock=ALPHA", files: {} });
  const r = await w.win.fetch("out/shareholding.json"); eq(r.ok, false);

  // 6. a later route the in-memory documents cannot serve reloads once; a core stock does not
  w = world({ hash: "#stock=ALPHA", files: files(mono("historical"), shard("historical", "ALPHA"), shard("historical", "BETA")) });
  await get(w, "historical");
  w.go("#stock=TCS"); eq(w.state.reloads, 0, "a development stock is already in the document");
  w.go("#stock=ALPHA"); eq(w.state.reloads, 0, "the same stock again");
  w.go("#stock=BETA"); eq(w.state.reloads, 1, "a stock the document lacks: one reload");
  w = world({ hash: "#stock=ALPHA", files: files(mono("historical"), shard("historical", "ALPHA")) });
  await get(w, "historical"); w.go(""); eq(w.state.reloads, 1, "back to the home page after a stock file was added: one reload for clean data");
  w = world({ hash: "#stock=TCS", files: files(mono("historical")) });
  await get(w, "historical"); w.go(""); eq(w.state.reloads, 0, "nothing was added, so nothing to clean");
  w = world({ hash: "", files: files(mono("historical")) });
  await get(w, "historical"); w.go("#stock=ALPHA"); eq(w.state.reloads, 0, "the first stock opened from the home page needs no reload (modules fetch later)");

  // 7. compare: up to five stocks, extra ignored, one request each
  w = world({ hash: "#compare=TCS,ALPHA,BETA,C,D,E,F", files: files(mono("historical"), shard("historical", "ALPHA"), shard("historical", "BETA")) });
  eq(w.win.SLShards.routeSymbols("#compare=TCS,ALPHA,BETA,C,D,E,F"), ["TCS", "ALPHA", "BETA", "C", "D"]);
  d = await get(w, "historical"); eq(Object.keys(d.stocks).sort(), ["ALPHA", "BETA", "TCS"]);
  eq(w.calls.filter((u) => u.includes("by_symbol")).length, 4, "ALPHA, BETA, C, D (E and F are ignored; TCS is in the single file)");

  // 8. hostile or malformed symbols never become a request
  const R = world().win.SLShards;
  for (const h of ["#stock=../x", "#stock=a/b", "#stock=%E0%A4%A", "#stock=", "#compare=../x,..", "#stock=" + "A".repeat(40)]) eq(R.routeSymbols(h), [], h);
  eq(R.routeSymbols("#stock=tcs"), ["TCS"]); eq(R.routeSymbols("#stock=M%26M"), ["M&M"]); eq(R.routeSymbols("#stock=ABC&section=cash"), ["ABC"]);
  w = world({ hash: "#stock=../etc", files: files(mono("historical")) }); await get(w, "historical"); eq(w.calls, ["out/historical.json"]);

  // 9. the layer holds no stock list, no sector and no recommendation wording
  ok(!/\b(buy|sell|target|rating|live)\b/i.test(m[1].replace(/live,|"live"/g, "")), "no advice or live wording");
  ok(!/(RELIANCE|INFY|HDFCBANK)/.test(m[1]), "no hard-coded stock list");
  console.log("Per-stock file layer tests passed (" + checks + " checks)");
})().catch((e) => { console.error(e); process.exit(1); });
