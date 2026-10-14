"use strict";
/* Phase 3: the opt-in local live layer in index.html. No network, no browser: the module runs in a vm with a fake DOM. */
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const ROOT = path.join(__dirname, ".."), html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const FIX = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "live_snapshots.json"), "utf8"));
let checks = 0;
const ok = (c, m) => { checks++; if (!c) { console.error("LIVE CLIENT TEST FAILED: " + m); process.exit(1); } };
const eq = (a, b, m) => { checks++; try { assert.deepStrictEqual(a, b); } catch (e) { console.error("LIVE CLIENT TEST FAILED: " + m + "\n" + e.message); process.exit(1); } };

const TAG = '<script type="module" id="stocklens-live">';
ok(html.split(TAG).length === 2, "exactly one live module");
const code = html.split(TAG)[1].split("</script>")[0];

/* ---- static checks ---- */
ok(!/innerHTML|outerHTML|insertAdjacent|\beval\s*\(|new Function|document\.write|javascript:/.test(code), "no HTML-injection or code-evaluation sinks");
ok((code.match(/\bfetch\(/g) || []).length === 1, "exactly one fetch call");
ok(!/XMLHttpRequest|WebSocket|sendBeacon|EventSource|importScripts|localStorage|sessionStorage|document\.cookie/.test(code), "no other network or storage API");
ok([...code.matchAll(/https?:\/\/[^\s"'<)]+/g)].every((m) => m[0] === "http://127.0.0.1:" || m[0] === "https://stocklens-live.onrender.com"), "the only web addresses are the loopback relay and the ONE fixed hosted test origin (the production hosted address is configuration, shipped empty; the page host is a bare host name)");
ok((code.match(/https:\/\/stocklens-live\.onrender\.com/g) || []).length === 1, "the hosted test origin appears exactly once, as one constant");
ok(!/token|secret|apikey|api_key|bearer|authorization|password|credential/i.test(code.replace(/\/\*[\s\S]*?\*\//g, "").replace("Upstox market data feed V3 (ltpc)", "").replace('credentials:"omit"', "")), "no token, key or credential words");
ok(!/\b(buy|sell|target price|rating|recommend)/i.test(code), "no advice language");
ok(!/credentials:"omit"/.test("") && /credentials:"omit"/.test(code) && /redirect:"error"/.test(code) && /cache:"no-store"/.test(code), "fetch options pinned");

/* ---- fake DOM ---- */
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parentNode = null; this.className = ""; this._t = ""; this.attrs = {}; this.id = ""; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  get firstChild() { return this.children[0] || null; }
  querySelector(sel) { const m = /^\[([a-z-]+)\]$/.exec(sel); const f = (n) => { for (const c of n.children) { if (m && c.attrs[m[1]] !== undefined) return c; const r = f(c); if (r) return r; } return null; }; return f(this); }
  all(fn, out = []) { for (const c of this.children) { if (fn(c)) out.push(c); c.all(fn, out); } return out; }
}
function makeDoc() {
  const calls = { create: 0 }, doc = { head: new El("head"), documentElement: new El("html"), visibilityState: "visible", byId: {} };
  doc.createElement = (t) => { calls.create++; return new El(t); };
  doc.getElementById = (id) => doc.byId[id] || null;
  for (const id of ["market", "mk-today", "mk-breadth", "mk-movers"]) { const e = new El("section"); e.id = id; doc.byId[id] = e; }
  for (const id of ["mk-today", "mk-breadth", "mk-movers"]) { const eod = new El("div"); eod.setAttribute("data-mk", "1"); eod.textContent = "EOD " + id; doc.byId[id].appendChild(eod); doc.byId["market"].appendChild(doc.byId[id]); }
  doc.calls = calls; return doc;
}
function load(loc, fetchImpl) {
  const doc = makeDoc(), fetches = [], timers = [];
  const win = { location: loc, setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; } };
  const ctx = { window: win, document: doc, URLSearchParams, Date, JSON, Math, Object, Array, Number, String, isFinite, encodeURIComponent, setTimeout: (f, ms) => { timers.push([f, ms]); return 1; }, clearTimeout() {},
    fetch: (u, o) => { fetches.push([u, o]); return fetchImpl ? fetchImpl(u, o) : Promise.reject(new Error("no")); } };
  win.window = win; vm.createContext(ctx); vm.runInContext(code, ctx);
  return { win, doc, fetches, timers, L: win.SLLive };
}

/* ---- gate ---- */
const loc = (host, search) => ({ hostname: host, search });
{
  const g = load(loc("example.com", "?live=local")).L.gate;
  const cases = [
    [loc("example.com", "?live=local"), null], [loc("stock.github.io", "?live=local"), null], [loc("192.168.1.5", "?live=local"), null], [loc("0.0.0.0", "?live=local"), null],
    [loc("127.0.0.1", ""), null], [loc("127.0.0.1", "?live=1"), null], [loc("127.0.0.1", "?live=LOCAL"), null], [loc("127.0.0.1", "?live="), null], [loc("127.0.0.1", "?x=live=local"), null],
    [loc("127.0.0.1", "?live=local&live_port=80"), null], [loc("127.0.0.1", "?live=local&live_port=99999"), null], [loc("127.0.0.1", "?live=local&live_port=abc"), null], [loc("127.0.0.1", "?live=local&live_port=8765.0"), null],
    [loc("127.0.0.1", "?live=local&live_port=1023"), null], [loc("127.0.0.1", "?live=local&live_port="), null], [null, null], [{}, null],
    [loc("127.0.0.1", "?live=local"), { port: 8765, url: "http://127.0.0.1:8765/v1/live/snapshot.json" }],
    [loc("localhost", "?live=local"), { port: 8765, url: "http://127.0.0.1:8765/v1/live/snapshot.json" }],
    [loc("LOCALHOST", "?live=local"), { port: 8765, url: "http://127.0.0.1:8765/v1/live/snapshot.json" }],
    [loc("[::1]", "?live=local"), { port: 8765, url: "http://127.0.0.1:8765/v1/live/snapshot.json" }],
    [loc("127.0.0.1", "?live=local&live_port=9000"), { port: 9000, url: "http://127.0.0.1:9000/v1/live/snapshot.json" }],
    [loc("127.0.0.1", "?a=1&live=local"), { port: 8765, url: "http://127.0.0.1:8765/v1/live/snapshot.json" }]];
  for (const [l, want] of cases) eq(JSON.parse(JSON.stringify(g(l))), want, "gate " + JSON.stringify(l));
}

/* ---- disabled by default: zero requests, zero DOM work ---- */
for (const l of [loc("example.com", ""), loc("example.com", "?live=local"), loc("127.0.0.1", ""), loc("localhost", "?live=1")]) {
  const r = load(l); eq(r.fetches.length, 0, "no request when disabled " + JSON.stringify(l)); eq(r.doc.calls.create, 0, "no DOM node created"); eq(r.timers.length, 0, "no timer scheduled"); eq(r.doc.head.children.length, 0, "no style injected");
}

/* ---- parity with the Python validator and client_accepts ---- */
{
  const L = load(loc("example.com", "")).L;
  for (const c of FIX.cases) {
    const p = L.validate(c.doc);
    ok((p.length === 0) === c.valid, "validate parity: " + c.name + " " + JSON.stringify(p.slice(0, 2)));
    const verdict = (a) => Object.fromEntries(Object.entries(a).map(([k, v]) => [k, !!v[0]]));
    eq(verdict(L.accepts(c.doc, c.now)), verdict(c.accepts), "accepts verdict parity (reason wording may differ): " + c.name);
  }
  ok(FIX.cases.length > 30, "enough parity cases");
  ok(FIX.cases.some((c) => c.accepts.indices[0]) && FIX.cases.some((c) => !c.accepts.indices[0]), "both outcomes are covered");
  const ts = FIX.cases.filter((c) => c.doc && c.doc.scope && c.doc.scope.universe === "test_set"); ok(ts.length > 0, "a test_set case exists");
  for (const c of ts) ok(!Object.values(c.accepts).some((v) => v[0]), "test_set is refused in full: " + c.name);
  /* raw bodies never reach the live state */
  for (const r of FIX.raw) { const v = new L.Controller().ingest({ status: 200, text: r.text }, FIX.base_ms); ok(v.state !== "live", "raw case never live: " + r.name); }
}

/* ---- controller ---- */
{
  const L = load(loc("example.com", "")).L, good = FIX.cases.find((c) => c.name === "good nifty500"), now = good.now;
  const withTs = (ts) => { const d = JSON.parse(JSON.stringify(good.doc)); d.server_ts_ms = ts; d.generated_at = new Date(ts + 19800000).toISOString().slice(0, 23) + "+05:30"; return d; };
  const res = (d, n) => ({ status: 200, text: JSON.stringify(d), dateMs: n });
  let c = new L.Controller();
  eq(c.ingest(res(good.doc, now), now).state, "live", "good snapshot is live");
  eq(c.ingest(res(withTs(good.doc.server_ts_ms - 1000), now), now).state, "hidden", "an older timestamp is refused");
  eq(c.ingest(res(withTs(good.doc.server_ts_ms + 1000), now + 1000), now + 1000).state, "live", "a newer one is accepted");
  c = new L.Controller(); c.ingest(res(good.doc, now), now);
  eq(c.ingest(res(good.doc, now + 16000), now + 16000).state === "live" ? "live" : "hidden", "hidden", "a snapshot that stops advancing is hidden (and is also too old)");
  eq(c.ingest({ error: "request failed" }, now).state, "unavailable", "a failed request is unavailable");
  eq(c.ingest({ status: 500, error: "HTTP 500" }, now).state, "unavailable", "HTTP errors are unavailable");
  eq(c.ingest({ status: 200, text: "x".repeat(70000) }, now).state, "hidden", "oversize body refused");
  eq(c.ingest({ status: 200, text: "{bad" }, now).state, "hidden", "bad JSON refused");
  /* stale by the relay's Date header, even if the local clock says fresh */
  c = new L.Controller(); eq(c.ingest(res(good.doc, now + 60000), now).state, "hidden", "age is measured against the relay's clock");
  /* relay restart: lower seq but newer timestamp is fine */
  c = new L.Controller(); const a = withTs(now - 2000); a.seq = 500; c.ingest(res(a, now), now); const b = withTs(now - 1000); b.seq = 1; eq(c.ingest(res(b, now), now).state, "live", "restart with a newer timestamp is tolerated");
}

/* ---- private test set: accepted ONLY with ?live=local&live_set=private_test on a loopback page ---- */
{
  const L = load(loc("example.com", "")).L, g = L.gate, U = "http://127.0.0.1:8765/v1/live/snapshot.json";
  const gj = (l) => JSON.parse(JSON.stringify(g(l)));
  eq(gj(loc("127.0.0.1", "?live=local")), { port: 8765, url: U }, "no private test without the option (the answer is exactly the old one)");
  for (const h of ["127.0.0.1", "localhost", "[::1]"]) eq(gj(loc(h, "?live=local&live_set=private_test")), { port: 8765, url: U, privateTest: true }, "private test on " + h);
  eq(gj(loc("127.0.0.1", "?live_set=private_test&live=local&live_port=9000")).privateTest, true, "order does not matter");
  for (const s of ["?live_set=private_test", "?live=1&live_set=private_test", "?live=LOCAL&live_set=private_test"]) eq(g(loc("127.0.0.1", s)), null, "the option alone opens nothing: " + s);
  for (const h of ["example.com", "stock.github.io", "192.168.1.5", "0.0.0.0"]) eq(g(loc(h, "?live=local&live_set=private_test")), null, "not on a non-loopback host: " + h);
  for (const v of ["1", "true", "PRIVATE_TEST", "private_test ", "private", ""]) eq(gj(loc("127.0.0.1", "?live=local&live_set=" + v)).privateTest, undefined, "other value stays off: '" + v + "'");

  const base = FIX.cases.find((c) => c.name === "test_set universe"), good = FIX.cases.find((c) => c.name === "good nifty500"), now = base.now;
  const clone = (d) => JSON.parse(JSON.stringify(d)), PT = { privateTest: true };
  const ts = base.doc, v0 = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, !!v[0]]));
  /* production path: test_set refused in full, with or without a non-true option */
  eq(v0(L.accepts(ts, now)), { indices: false, breadth: false, movers: false, prices: false }, "production path refuses test_set");
  for (const o of [undefined, null, {}, { privateTest: false }, { privateTest: "true" }, { privateTest: 1 }, { privateTest: "private_test" }]) eq(v0(L.accepts(ts, now, o)), { indices: false, breadth: false, movers: false, prices: false }, "refused with option " + JSON.stringify(o));
  ok(/few instruments/.test(L.accepts(ts, now).indices[1]), "the production reason is unchanged");
  /* private path: only indices and prices */
  eq(v0(L.accepts(ts, now, PT)), { indices: true, breadth: false, movers: false, prices: true }, "private path shows only indices and prices");
  ok(/not a market breadth/.test(L.accepts(ts, now, PT).breadth[1]), "breadth reason");
  /* every safeguard still applies with the option on */
  const none = (d, n, why) => eq(v0(L.accepts(d, n, PT)), { indices: false, breadth: false, movers: false, prices: false }, "still refused: " + why);
  none(ts, base.doc.server_ts_ms + 20001, "older than 20 s"); none(ts, base.doc.server_ts_ms - 5001, "from the future");
  for (const st of ["closed", "pre_open", "unknown"]) { const d = clone(ts); d.market.status = st; none(d, now, "market " + st); }
  { const d = clone(ts); d.quality.ok = false; d.quality.reasons = ["x"]; none(d, now, "relay reports a problem"); }
  { const d = clone(ts); d.extra = 1; none(d, now, "extra key"); }
  { const d = clone(ts); d.private_test = true; none(d, now, "a snapshot cannot opt itself in"); }
  { const d = clone(ts); d.schema = 2; none(d, now, "wrong schema"); }
  { const d = clone(ts); d.indices[0].ltp = -1; none(d, now, "bad index price"); }
  { const d = clone(ts); d.scope.universe = "nope"; none(d, now, "unknown universe"); }
  { const d = clone(ts); d.indices.pop(); eq(v0(L.accepts(d, now, PT)).indices, false, "a missing index is not shown"); }
  { const d = clone(ts); d.prices = {}; eq(v0(L.accepts(d, now, PT)).prices, false, "no prices is not shown"); }
  /* real universes: the option changes nothing */
  for (const c of FIX.cases) if (c.doc && c.doc.scope && c.doc.scope.universe !== "test_set") eq(v0(L.accepts(c.doc, c.now, PT)), v0(L.accepts(c.doc, c.now)), "option changes nothing for " + c.name);

  /* controller */
  const res = (d, n) => ({ status: 200, text: JSON.stringify(d), dateMs: n });
  eq(new L.Controller().ingest(res(ts, now), now).state, "hidden", "default controller hides test_set");
  eq(new L.Controller({ privateTest: "true" }).ingest(res(ts, now), now).state, "hidden", "non-true option hides test_set");
  let c = new L.Controller({ privateTest: true }), v = c.ingest(res(ts, now), now);
  eq([v.state, v.kind], ["live", "live"], "private controller shows test_set"); eq(v0(v.blocks), { indices: true, breadth: false, movers: false, prices: true }, "only two blocks");
  { const o = clone(ts); o.server_ts_ms -= 1000; o.generated_at = new Date(o.server_ts_ms + 19800000).toISOString().slice(0, 23) + "+05:30"; eq(c.ingest(res(o, now), now).state, "hidden", "private: an older timestamp is refused (sequence)"); }
  { const d = clone(ts); d.market.status = "closed"; eq(new L.Controller({ privateTest: true }).ingest(res(d, now), now).kind, "closed", "private: closed market is CLOSED"); }
  eq(new L.Controller({ privateTest: true }).ingest(res(ts, base.doc.server_ts_ms + 60000), base.doc.server_ts_ms + 60000).state, "hidden", "private: an old snapshot is not live");
  { const d = clone(ts); d.quality.ok = false; d.quality.reasons = ["feed_silent"]; eq(new L.Controller({ privateTest: true }).ingest(res(d, now), now).state, "hidden", "private: an unhealthy relay is hidden"); }
  c = new L.Controller({ privateTest: true }); c.ingest(res(ts, now), now);
  const f = c.ingest({ error: "request failed" }, now + 1000); eq([f.state, f.kind], ["unavailable", "stale"], "private: a failed poll keeps the last values only as STALE");
  eq(new L.Controller({ privateTest: true }).ingest(res(good.doc, good.now), good.now).state, "live", "private controller still handles a real universe");

  /* rendering */
  const mk = (priv) => { const doc = makeDoc(); return { doc, ui: new L.Ui(doc, priv), panels: () => doc.byId["market"].all((n) => n.attrs["data-lv"]).map((n) => n.attrs["data-lv"]) }; };
  const eod = (doc) => ["mk-today", "mk-breadth", "mk-movers"].map((id) => doc.byId[id].querySelector("[data-mk]").textContent);
  { const r = mk(true), before = eod(r.doc), pc = new L.Controller({ privateTest: true });
    r.ui.update(pc.ingest(res(ts, now), now));
    eq(r.panels(), ["private-test"], "one separate panel, no indices/breadth/movers panels");
    const txt = r.doc.byId["market"].textContent;
    ok(/PRIVATE TEST SET — not a market dashboard/.test(txt), "labelled private test set"); ok(/NIFTY 50/.test(txt) && /TST00001/.test(txt) && /TST00003/.test(txt), "instruments listed");
    ok(/-0\.40%/.test(txt) && /09:59:59/.test(txt), "percentage change and last-trade time shown");
    eq(r.doc.byId["mk-today"].all((n) => n.attrs["data-lv"]).length, 0, "nothing inside the Market Today card"); eq(r.doc.byId["mk-breadth"].all((n) => n.attrs["data-lv"]).length + r.doc.byId["mk-movers"].all((n) => n.attrs["data-lv"]).length, 0, "no breadth or movers");
    ok(!/Advances|Live gainers|Live losers/.test(txt), "no breadth or rankings text"); eq(eod(r.doc), before, "EOD content untouched");
    ok(/^LOCAL LIVE TEST · LIVE/.test(r.doc.byId["market"].firstChild.textContent) && /PRIVATE TEST SET — not a market dashboard/.test(r.doc.byId["market"].firstChild.textContent), "chip says LOCAL LIVE TEST and private test set");
    r.ui.update(pc.ingest({ error: "request failed" }, now + 1000)); eq(r.panels(), ["private-test"], "stale: the private panel stays, dashed"); ok(/STALE/.test(r.doc.byId["market"].textContent) && /not current/.test(r.doc.byId["market"].textContent), "labelled STALE, not current");
    eq(r.doc.byId["mk-today"].all((n) => n.attrs["data-lv"]).length, 0, "stale: still not in the Market Today card");
    r.ui.update(new L.Controller({ privateTest: true }).ingest({ error: "request failed" }, now)); eq(r.panels(), [], "unavailable: panel removed"); ok(/not reachable/.test(r.doc.byId["market"].textContent), "unavailable text");
    const cl = clone(ts); cl.market.status = "closed"; r.ui.update(new L.Controller({ privateTest: true }).ingest(res(cl, now), now)); eq(r.panels(), [], "closed: no panel"); ok(/CLOSED/.test(r.doc.byId["market"].textContent), "closed label"); eq(eod(r.doc), before, "EOD still untouched"); }
  { const r = mk(false); r.ui.update({ state: "live", kind: "live", reason: "", blocks: { indices: [true, ""], breadth: [false, "x"], movers: [false, "x"], prices: [true, ""] }, doc: ts, last: null });
    ok(!r.panels().includes("private-test"), "a Ui without the option never draws the private panel"); }
  { const evil = clone(ts); evil.indices[0].name = "<img src=x onerror=alert(1)>"; const r = mk(true); r.ui.update(new L.Controller({ privateTest: true }).ingest(res(evil, now), now));
    ok(r.doc.byId["market"].textContent.includes("<img src=x onerror=alert(1)>") && r.doc.byId["market"].all((n) => n.tag === "img").length === 0, "markup in the private panel stays text"); }
  /* end to end through start(): only the explicit option reaches the private panel */
  { const rr = load(loc("127.0.0.1", "?live=local&live_set=private_test"), () => new Promise(() => {})); eq(rr.fetches.length, 1, "one request"); eq(rr.fetches[0][0], U, "same fixed loopback address"); }
}

/* ---- LOCAL LIVE TEST panel: labels, Prev close, Age, no recent trade, relay quality, 5 s poll (private option only) ---- */
{
  const L = load(loc("example.com", "")).L, base = FIX.cases.find((c) => c.name === "test_set universe"), ts = base.doc, now = base.now;
  const clone = (d) => JSON.parse(JSON.stringify(d)), res = (d, n) => ({ status: 200, text: JSON.stringify(d), dateMs: n });
  const isoOf = (ms) => new Date(ms + 19800000).toISOString().slice(0, 23) + "+05:30", T = ts.server_ts_ms;
  const withLtt = (fn) => { const d = clone(ts); fn(d); return d; };
  const mk = () => { const doc = makeDoc(), ui = new L.Ui(doc, true); return { doc, ui, text: () => doc.byId["market"].textContent, panel: () => doc.byId["market"].all((n) => n.attrs["data-lv"] === "private-test")[0] }; };
  const show = (d) => { const r = mk(); r.ui.update(new L.Controller({ privateTest: true }).ingest(res(d, T + 1000), T + 1000)); return r; };
  const cells = (r, rowName) => { const row = r.panel().all((n) => n.tag === "tr").find((tr) => tr.children[0] && tr.children[0].textContent === rowName); return row ? row.children.map((c) => c.textContent) : null; };

  /* labels */
  { const r = show(ts), t = r.text();
    ok(/LOCAL LIVE TEST/.test(r.panel().textContent) && /PRIVATE TEST SET — not a market dashboard/.test(r.panel().textContent), "panel is labelled LOCAL LIVE TEST and private test set");
    ok(/^LOCAL LIVE TEST · LIVE · updated /.test(r.doc.byId["market"].firstChild.textContent), "chip is labelled LOCAL LIVE TEST");
    const head = r.panel().all((n) => n.tag === "th").map((n) => n.textContent);
    eq(head, ["Instrument", "Last price", "Prev close", "Change", "Last trade (IST)", "Age"], "columns");
    /* Prev close from cp, change from the snapshot's own change_pct */
    const nifty = cells(r, "NIFTY 50"); ok(nifty && /20,000\.00/.test(nifty[2]), "NIFTY 50 previous close shown: " + JSON.stringify(nifty)); ok(/-0\.40%/.test(nifty[3]), "change % shown");
    ok(/09:59:59/.test(nifty[4]), "last trade time shown"); }
  { const doc = makeDoc(), ui = new L.Ui(doc, true); ui.mount(); eq(doc.byId["market"].firstChild.textContent, "LOCAL LIVE TEST: connecting…", "the first chip says LOCAL LIVE TEST");
    const d2 = makeDoc(), u2 = new L.Ui(d2); u2.mount(); eq(d2.byId["market"].firstChild.textContent, "Live (private test): connecting…", "an ordinary Ui keeps its first chip"); }
  /* a non-private Ui keeps its old labels */
  { const doc = makeDoc(), ui = new L.Ui(doc); ui.update(new L.Controller().ingest({ error: "request failed" }, now)); ok(/^Live \(private test\)/.test(doc.byId["market"].firstChild.textContent), "ordinary Ui keeps its label"); }
  { const r = mk(); r.ui.update(new L.Controller({ privateTest: true }).ingest({ error: "request failed" }, now)); ok(/^LOCAL LIVE TEST · UNAVAILABLE/.test(r.doc.byId["market"].firstChild.textContent) && /not reachable/.test(r.text()), "disconnected state labelled LOCAL LIVE TEST"); }

  /* age and "no recent trade": computed only from the snapshot's own timestamps */
  const ageFor = (deltaMs) => { const d = withLtt((x) => { x.indices[0].ltt = isoOf(T - deltaMs); x.indices[0].cp = x.indices[0].cp; }); const c = cells(show(d), "NIFTY 50"); return c; };
  eq(ageFor(0)[5], "0 s", "age 0 s"); eq(ageFor(12000)[5], "12 s", "age 12 s"); eq(ageFor(59000)[5], "59 s", "age 59 s");
  eq(ageFor(60000)[5], "1 m 00 s", "exactly 60 s is not yet flagged"); eq(ageFor(61000)[5], "1 m 01 s · no recent trade", "61 s is flagged");
  eq(ageFor(3700000)[5], "1 h 01 m · no recent trade", "an hour-old trade is flagged");
  { const r = show(withLtt((x) => { x.indices[0].ltt = isoOf(T - 61000); })); const flagged = r.panel().all((n) => n.attrs["data-lv-flag"] === "no-recent-trade"); eq(flagged.length, 1, "exactly the stale row carries the flag"); }
  { const r = show(withLtt((x) => { x.indices[0].ltt = isoOf(T - 5000); })); eq(r.panel().all((n) => n.attrs["data-lv-flag"]).length, 0, "no flag on a fresh trade"); }
  /* missing or unreadable timestamps: a dash, never a number */
  { const c = ageFor(0); const m = cells(show(withLtt((x) => { x.indices[0].ltt = null; })), "NIFTY 50"); eq([m[4], m[5]], ["–", "–"], "missing ltt: dashes"); ok(!/no recent trade/.test(m.join("")), "missing ltt is not called a stale trade");
    const g = cells(show(withLtt((x) => { x.indices[0].ltt = "not-a-timestamp-at-all"; })), "NIFTY 50"); eq([g[4], g[5]], ["–", "–"], "unreadable ltt: dashes");
    const f = cells(show(withLtt((x) => { x.indices[0].ltt = isoOf(T + 10000); })), "NIFTY 50"); eq(f[5], "–", "a trade time well in the future gives no age");
    const f2 = cells(show(withLtt((x) => { x.indices[0].ltt = isoOf(T + 2000); })), "NIFTY 50"); eq(f2[5], "0 s", "a tiny clock skew is shown as 0 s");
    const nocp = cells(show(withLtt((x) => { x.indices[0].cp = null; x.indices[0].change = null; x.indices[0].change_pct = null; })), "NIFTY 50"); eq([nocp[2], nocp[3]], ["–", "–"], "no previous close: dashes"); }
  { const sym = Object.keys(ts.prices)[0], r = show(withLtt((x) => { x.prices[sym].ltt = isoOf(T - 90000); x.prices[sym].cp = null; x.prices[sym].change_pct = null; })), c = cells(r, sym);
    eq(c[2], "–", "equity without a previous close: dash"); ok(/1 m 30 s · no recent trade/.test(c[5]), "equity row is flagged on its own"); }

  /* relay quality line */
  { const r = show(ts); ok(/Relay quality: ok · warnings: indices_unresolved:4\./.test(r.text()), "quality ok and warnings shown");
    const r2 = show(withLtt((x) => { x.quality.warnings = []; })); ok(/Relay quality: ok · warnings: none\./.test(r2.text()), "no warnings said in words"); }
  { const bad = withLtt((x) => { x.quality.ok = false; x.quality.reasons = ["feed_silent", "low_coverage"]; }), ui = new L.Ui(makeDoc(), true);
    ok(/Relay quality: NOT OK — feed_silent, low_coverage/.test(ui.privatePanel(bad, "STALE").textContent), "reasons shown when quality is not ok");
    const r = mk(); r.ui.update(new L.Controller({ privateTest: true }).ingest(res(bad, T + 1000), T + 1000)); eq(r.doc.byId["market"].all((n) => n.attrs["data-lv"]).length, 0, "an unhealthy snapshot is never drawn as live"); }

  /* polling: 5 s only with the explicit private option */
  { const iv = (search) => { const r = load(loc("127.0.0.1", search), () => new Promise(() => {})); const last = r.timers[r.timers.length - 1]; r.timers[0][0](); return [last[1], r.timers[r.timers.length - 1][1]]; };
    eq(iv("?live=local&live_set=private_test"), [5000, 5000], "private option polls every 5 s and re-arms at 5 s");
    eq(iv("?live=local"), [60000, 60000], "ordinary local live stays at 60 s");
    eq(iv("?live=local&live_set=1"), [60000, 60000], "any other live_set value stays at 60 s");
    eq(iv("?live=local&live_set=PRIVATE_TEST"), [60000, 60000], "a different case stays at 60 s");
    eq(L.CFG.poll, 60000, "the ordinary interval is unchanged"); eq(L.CFG.pollTest, 5000, "test interval"); }
  /* still one request at a time, one fixed loopback address */
  { const r = load(loc("127.0.0.1", "?live=local&live_set=private_test"), () => new Promise(() => {})); eq(r.fetches.length, 1, "one request at start"); r.timers[0][0](); eq(r.fetches.length, 1, "a tick while a request is in flight starts nothing"); }
}

/* ---- rendering ---- */
{
  const r = load(loc("127.0.0.1", "?live=local"), () => new Promise(() => {})), good = FIX.cases.find((c) => c.name === "good nifty500"), now = good.now;
  eq(r.fetches.length, 1, "one request when enabled"); eq(r.fetches[0][0], "http://127.0.0.1:8765/v1/live/snapshot.json", "the fixed address");
  eq(r.fetches[0][1].credentials, "omit", "no credentials"); eq(r.fetches[0][1].cache, "no-store", "no cache");
  const L = r.L, doc = makeDoc(), ui = new L.Ui(doc), ctl = new L.Controller();
  const eodText = () => ["mk-today", "mk-breadth", "mk-movers"].map((id) => doc.byId[id].querySelector("[data-mk]").textContent);
  const before = eodText();
  const panels = () => doc.byId["market"].all((n) => n.attrs["data-lv"]).map((n) => n.attrs["data-lv"]);
  ui.update(ctl.ingest({ status: 200, text: JSON.stringify(good.doc), dateMs: now }, now));
  eq(panels(), ["indices", "breadth", "movers"], "three panels drawn");
  eq(eodText(), before, "EOD content untouched");
  for (const id of ["mk-today", "mk-breadth", "mk-movers"]) { const s = doc.byId[id]; ok(s.children[s.children.length - 1].attrs["data-mk"] !== undefined, "EOD box stays last in " + id); ok(s.children[s.children.length - 2].attrs["data-lv"], "live panel sits before the EOD box in " + id); }
  ok(/^Live \(private test\)/.test(doc.byId["market"].firstChild.textContent), "status chip first");
  /* escaping: markup in text stays text */
  const evil = JSON.parse(JSON.stringify(good.doc)); evil.indices[0].name = "<img src=x onerror=alert(1)>"; evil.gainers[0].symbol = "<img src=x onerror=alert(1)>";
  const doc2 = makeDoc(), ui2 = new L.Ui(doc2); ui2.update({ state: "live", kind: "live", reason: "", blocks: { indices: [true, ""], breadth: [false, "x"], movers: [true, ""], prices: [true, ""] }, doc: evil });
  ok(doc2.byId["market"].textContent.includes("<img src=x onerror=alert(1)>"), "markup is shown as text"); ok(doc2.byId["market"].all((n) => n.tag === "img").length === 0, "no element is created from it");
  /* failure removes every panel */
  ui.update(new L.Controller().ingest({ error: "request failed" }, now)); eq(panels(), [], "panels removed when the relay fails (nothing earlier to keep)"); ok(/not reachable/.test(doc.byId["market"].firstChild.textContent), "chip says so"); eq(eodText(), before, "EOD still intact");
  /* test_set / stale: hidden with reason */
  const ts = FIX.cases.find((c) => c.doc && c.doc.scope && c.doc.scope.universe === "test_set");
  const ui3 = new L.Ui(makeDoc()); ui3.update(new L.Controller().ingest({ status: 200, text: JSON.stringify(ts.doc), dateMs: ts.now }, ts.now)); eq(ui3.panels.indices || null, null, "no panel for test_set");
}

/* ---- tabs, labels, failures, refresh (Market Today NIFTY 50 / SENSEX) ---- */
{
  const r0 = load(loc("127.0.0.1", "?live=local"), () => new Promise(() => {})), L = r0.L, good = FIX.cases.find((c) => c.name === "good nifty500"), now = good.now;
  eq(L.CFG.poll, 60000, "polls every 60 seconds"); eq(r0.timers.map((t) => t[1]).filter((m) => m !== 4000), [60000], "one poll timer, 60 s (the 4 s one is the request timeout)");
  const res = (d, n) => ({ status: 200, text: JSON.stringify(d), dateMs: n });
  const withState = (f) => { const d = JSON.parse(JSON.stringify(good.doc)); f(d); return d; };
  const rows = good.doc.indices.map((x) => x.name); ok(rows.includes("NIFTY 50"), "fixture carries NIFTY 50"); ok(!rows.includes("SENSEX"), "fixture carries no SENSEX");
  const mk = () => { const doc = makeDoc(), ui = new L.Ui(doc); return { doc, ui, ctl: new L.Controller(), txt: () => doc.byId["mk-today"].all((n) => n.attrs["data-lv"] === "indices").map((n) => n.textContent).join("|"), panels: () => doc.byId["market"].all((n) => n.attrs["data-lv"]).map((n) => n.attrs["data-lv"]), chip: () => doc.byId["market"].firstChild.textContent }; };
  /* labels */
  eq(L.labelOf(null), "UNAVAILABLE", "no view"); eq(L.labelOf({ state: "live", kind: "live" }), "LIVE", "live"); eq(L.labelOf({ state: "hidden", kind: "stale" }), "STALE", "stale"); eq(L.labelOf({ state: "hidden", kind: "closed" }), "CLOSED", "closed"); eq(L.labelOf({ state: "unavailable", reason: "error" }), "UNAVAILABLE", "error");
  /* tab switching: NIFTY 50 shows its value, SENSEX shows the unavailable state, switching back restores it, no new request */
  { const t = mk(), v = t.ctl.ingest(res(good.doc, now), now); eq(v.kind, "live", "good is live"); t.ui.update(v);
    ok(/NIFTY 50/.test(t.txt()) && /LIVE/.test(t.txt()), "NIFTY 50 panel is live by default"); ok(/^Live \(private test\) · LIVE/.test(t.chip()), "chip says LIVE");
    t.ui.select("SENSEX"); ok(/SENSEX has no live value/.test(t.txt()), "SENSEX shows the unavailable state"); ok(!/\d{2},\d{3}/.test(t.txt().replace(/\d{2}:\d{2}:\d{2}/g, "")), "no number is invented for SENSEX"); eq(t.doc.byId["mk-today"].all((n) => n.attrs["data-lv-index"] === "SENSEX").length, 1, "one SENSEX panel");
    t.ui.select("NIFTY 50"); ok(/NIFTY 50/.test(t.txt()) && !/SENSEX/.test(t.txt()), "back to NIFTY 50"); eq(t.panels().filter((x) => x === "indices").length, 1, "never two index panels");
    t.ui.select("BOGUS"); eq(t.ui.sel, "NIFTY 50", "an unknown tab is ignored");
    eq(t.doc.byId["mk-today"].children[t.doc.byId["mk-today"].children.length - 1].attrs["data-mk"] !== undefined, true, "EOD box still last"); }
  /* the header says LIVE only when the selected index has its own row */
  { const head = (t) => t.doc.byId["mk-today"].all((n) => n.attrs["data-lv"] === "indices").map((n) => n.children[0].children[0].textContent)[0];
    const t = mk(); t.ui.update(t.ctl.ingest(res(good.doc, now), now));
    eq(head(t), "LIVE", "NIFTY 50 has a row: header says LIVE"); ok(/updated/.test(t.txt()) && !/No live value/.test(t.txt()), "and shows its value");
    t.ui.select("SENSEX"); eq(head(t), "No live value", "SENSEX has no row: header does not say LIVE"); ok(!/LIVE/.test(t.txt()), "the word LIVE is not in the SENSEX panel"); ok(/SENSEX has no live value: its data source has not been verified/.test(t.txt()), "the SENSEX explanation is kept");
    const noNifty = withState((x) => { x.indices = x.indices.filter((r) => r.name !== "NIFTY 50"); x.scope.indices_expected = x.indices.length; }); ok(L.validate(noNifty).length === 0, "snapshot without NIFTY 50 is valid");
    const u = mk(); u.ui.update(u.ctl.ingest(res(noNifty, now), now)); eq(head(u), "No live value", "NIFTY 50 row missing: header does not say LIVE"); ok(/NIFTY 50 is not in the live snapshot/.test(u.txt()) && !/LIVE/.test(u.txt()), "with its own explanation");
    u.ui.select("SENSEX"); eq(head(u), "No live value", "SENSEX stays no live value");
    const w = mk(); w.ui.update(w.ctl.ingest(res(noNifty, now + 60000), now + 60000)); eq(w.ctl.ingest(res(noNifty, now + 60000), now + 60000).kind, "stale", "stale snapshot without the row"); eq(head(w), "No live value", "a stale snapshot without the row does not say STALE either"); }
  /* a SENSEX row in a snapshot is shown only when the relay really supplies one */
  { const t = mk(), d = withState((x) => { x.indices.push({ name: "SENSEX", ltp: 80000, cp: 79000, change: 1000, change_pct: pct(80000, 79000), ltt: 1 }); x.scope.indices_expected = x.indices.length; });
    function pct(a, b) { return (a / b - 1) * 100; }
    ok(L.validate(d).length === 0, "snapshot with a SENSEX row is valid"); t.ui.update(t.ctl.ingest(res(d, now), now)); t.ui.select("SENSEX"); ok(/80,000\.00/.test(t.txt()), "a supplied SENSEX value is shown"); }
  /* stale: a valid but old snapshot is STALE, labelled, never LIVE, and carries its own time */
  { const t = mk(), v = t.ctl.ingest(res(good.doc, now + 60000), now + 60000); eq(v.kind, "stale", "40 s old is stale"); eq(v.state, "hidden", "and not live"); t.ui.update(v);
    ok(/^Live \(private test\) · STALE/.test(t.chip()), "chip says STALE"); ok(/STALE/.test(t.txt()) && !/LIVE · /.test(t.txt().replace("STALE", "")), "panel says STALE, not LIVE"); ok(/no longer current/.test(t.txt()), "panel says it is not current"); eq(t.panels(), ["indices"], "stale shows the index only, never breadth or movers"); }
  /* far too old: unavailable, not stale */
  { const t = mk(); eq(t.ctl.ingest(res(good.doc, now + 20 * 60000), now + 20 * 60000).kind, "unavailable", "20 min old is not shown"); }
  /* closed market */
  { const t = mk(), d = withState((x) => { x.market.status = "closed"; }), v = t.ctl.ingest(res(d, now), now); eq(v.kind, "closed", "closed market"); t.ui.update(v);
    ok(/CLOSED/.test(t.chip()) && /not live/.test(t.chip()), "chip says CLOSED and that EOD is not live"); eq(t.panels(), [], "no live panel when closed"); }
  /* failed requests: nothing earlier -> UNAVAILABLE; after a live reading -> STALE with the old value; after the keep period -> UNAVAILABLE */
  { const t = mk(); const v = t.ctl.ingest({ error: "request failed" }, now); eq(v.kind, "unavailable", "failed with nothing earlier"); t.ui.update(v); ok(/UNAVAILABLE/.test(t.chip()) && /not reachable/.test(t.chip()), "chip says UNAVAILABLE"); eq(t.panels(), [], "no panel");
    t.ui.update(t.ctl.ingest(res(good.doc, now), now)); eq(t.panels().includes("indices"), true, "live again");
    const f = t.ctl.ingest({ status: 503, error: "HTTP 503" }, now + 60000); eq(f.kind, "stale", "failure right after a live reading is STALE"); eq(f.state, "unavailable", "state stays unavailable"); t.ui.update(f);
    ok(/STALE/.test(t.chip()) && /not current/.test(t.chip()), "chip says STALE"); eq(t.panels(), ["indices"], "only the retained index value, marked stale");
    const g = t.ctl.ingest({ error: "request failed" }, now + L.CFG.staleKeep + 1); eq(g.kind, "unavailable", "after the keep period nothing is shown"); t.ui.update(g); eq(t.panels(), [], "panel gone"); }
  /* invalid / test / unhealthy snapshots are never LIVE or STALE */
  for (const [nm, f] of [["bad quality", (d) => { d.quality.ok = false; d.quality.reasons = ["x"]; }], ["test_set", (d) => { d.scope.universe = "test_set"; }]]) { const v = new L.Controller().ingest(res(withState(f), now), now); eq(v.kind, "unavailable", nm + " is unavailable"); }
  eq(new L.Controller().ingest({ status: 200, text: "{bad" }, now).kind, "unavailable", "bad JSON is unavailable");
}
{
  /* drive start() directly with a fake environment: overlap, completion, click */
  const good = FIX.cases.find((c) => c.name === "good nifty500"), now = good.now;
  const doc = makeDoc(), listeners = {}, timers = []; doc.addEventListener = (t, f) => { listeners[t] = f; };
  const pending = [];
  const calls = [], vmctx = { window: { location: loc("example.com", "") }, document: doc, URLSearchParams, Date, JSON, Math, Object, Array, Number, String, isFinite, encodeURIComponent, setTimeout: () => 1, clearTimeout() {}, AbortController: undefined,
    fetch: (u, o) => { calls.push(u); return new Promise((resolve) => { pending.push(resolve); }); } };
  vmctx.window.window = vmctx.window; vm.createContext(vmctx); vm.runInContext(code, vmctx);
  const S = vmctx.window.SLLive, env = { document: doc, setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; } };
  const out = S.start({ url: "http://127.0.0.1:8765/v1/live/snapshot.json" }, env);
  eq(calls.length, 1, "one request in flight"); eq(out.refresh(), false, "a second refresh is refused while one is in flight"); eq(out.refresh(), false, "and again"); eq(calls.length, 1, "still one request"); eq(timers.map((t) => t[1]), [60000], "next poll in 60 s");
  const body = { ok: true, status: 200, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(good.doc)) };
  pending[0](body);
  const settle = () => new Promise((rs) => setImmediate(rs));
  (async () => {
    await settle(); await settle(); await settle();
    eq(out.refresh(), true, "after completion a new request may start"); eq(calls.length, 2, "second request"); pending[1]({ ok: false, status: 503, headers: { get: () => null } }); await settle(); await settle(); await settle();
    eq(out.refresh(), true, "and after a failure too"); eq(calls.length, 3, "third request");
    timers[0][0](); eq(calls.length, 3, "a timer tick during an in-flight request starts nothing"); eq(timers[timers.length - 1][1], 60000, "and re-arms at 60 s");
    /* clicking a page tab switches the live panel without any request */
    const btn = { getAttribute: (k) => (k === "data-tab" ? "SENSEX" : null), parentNode: { getAttribute: (k) => (k === "data-mk-tabs" ? "index" : null) } };
    out.ui.update(out.controller.ingest({ status: 200, text: JSON.stringify(good.doc), dateMs: now }, now));
    listeners.click({ target: { closest: () => btn } }); eq(out.ui.sel, "SENSEX", "click selects SENSEX"); eq(calls.length, 3, "no request for a tab click");
    ok(/SENSEX has no live value/.test(doc.byId["mk-today"].all((x) => x.attrs["data-lv"] === "indices").map((x) => x.textContent).join("")), "panel shows SENSEX unavailable");
    listeners.click({ target: { closest: () => null } }); eq(out.ui.sel, "SENSEX", "a click elsewhere changes nothing");
    const other = { getAttribute: (k) => (k === "data-tab" ? "NIFTY 50" : null), parentNode: { getAttribute: () => "other" } }; listeners.click({ target: { closest: () => other } }); eq(out.ui.sel, "SENSEX", "another tab group is ignored");

    /* ============ Stage 4: the hosted (public) live gate. Shipped OFF; every case below injects its own configuration. ============ */
    {
      const L = load(loc("example.com", "")).L, base = FIX.cases.find((c) => c.name === "test_set universe"), clone = (d) => JSON.parse(JSON.stringify(d));
      const isoOf = (ms) => new Date(ms + 19800000).toISOString().slice(0, 23) + "+05:30";
      const SVC = "https://live.stocklens-example.com/v1/live/snapshot.json", ON = { enabled: true, url: SVC };
      const page = (over) => Object.assign({ protocol: "https:", hostname: "arnavdev11.github.io", port: "", search: "" }, over || {});
      const pubDoc = (d) => { const x = clone(d); x.breadth = { advances: 0, declines: 0, unchanged: 0, counted: 0 }; x.gainers = null; x.losers = null; return x; };
      const T0 = base.doc.server_ts_ms, now = T0 + 1000, res = (d, date, n) => ({ status: 200, text: JSON.stringify(d), dateMs: date });
      const pubCtl = () => new L.Controller({ privateTest: true, pub: true });
      eq(L.validate(pubDoc(base.doc)).length, 0, "the public view shape (zero breadth, null movers) validates");

      /* shipped configuration: off, and the page does nothing */
      eq(JSON.parse(JSON.stringify(L.PUBLIC_LIVE)), { enabled: false, url: "" }, "shipped with the public feed disabled and no address");
      for (const l of [page(), page({ search: "?live=local" }), page({ search: "?live_set=private_test&live=public" })]) {
        const r = load(l); eq(r.L.gate(l), null, "no gate on the real page by default"); eq(r.fetches.length, 0, "no request"); eq(r.doc.calls.create, 0, "no DOM node"); eq(r.timers.length, 0, "no timer"); }
      /* the gate */
      eq(JSON.parse(JSON.stringify(L.gate(page(), ON))), { url: SVC, pub: true, privateTest: true, port: null }, "enabled + exact origin + https address -> public gate");
      eq(L.gate(page({ search: "?live=local" }), ON) !== null, true, "the query string neither enables nor disables the public gate");
      const offs = [undefined, null, {}, { enabled: false, url: SVC }, { enabled: "true", url: SVC }, { enabled: 1, url: SVC }, { enabled: true }, { enabled: true, url: "" }, { enabled: true, url: 5 },
        { enabled: true, url: "http://live.stocklens-example.com/v1/live/snapshot.json" }, { enabled: true, url: "https://live.stocklens-example.com:8443/v1/live/snapshot.json" },
        { enabled: true, url: SVC + "?x=1" }, { enabled: true, url: SVC + "#x" }, { enabled: true, url: "https://live.stocklens-example.com/v1/live/other.json" }, { enabled: true, url: "https://live.stocklens-example.com/" },
        { enabled: true, url: "https://live.stocklens-example.com/v1/live/snapshot.json/" }, { enabled: true, url: "https://user@live.stocklens-example.com/v1/live/snapshot.json" },
        { enabled: true, url: "https://1.2.3.4/v1/live/snapshot.json" }, { enabled: true, url: "https://localhost/v1/live/snapshot.json" }, { enabled: true, url: "https://LIVE.example.com/v1/live/snapshot.json" },
        { enabled: true, url: "https://arnavdev11.github.io/v1/live/snapshot.json" }, { enabled: true, url: "//live.example.com/v1/live/snapshot.json" }, { enabled: true, url: " " + SVC },
        { enabled: true, url: "https://live.example.com/v1/live/snapshot.json\n" }];
      for (const c of offs) eq(L.gate(page(), c), null, "no public gate for config " + JSON.stringify(c));
      for (const l of [page({ protocol: "http:" }), page({ hostname: "evil.github.io" }), page({ hostname: "arnavdev11.github.io.evil.com" }), page({ hostname: "evil-arnavdev11.github.io" }), page({ port: "8443" }), page({ hostname: "example.com" }),
        page({ hostname: "" }), { hostname: 5, search: "" }, null, undefined])
        eq(L.gate(l, ON), null, "no public gate from page " + JSON.stringify(l));
      eq(L.gate(loc("127.0.0.1", ""), ON), null, "a loopback page never takes the public gate");
      eq(L.gate(loc("127.0.0.1", "?live=local"), ON).port, 8765, "the local gate is unchanged");

      /* the controller in public mode */
      { const d = pubDoc(base.doc), ok1 = pubCtl().ingest(res(d, now), now);
        eq([ok1.state, ok1.kind, !!ok1.blocks.indices[0], !!ok1.blocks.prices[0], ok1.blocks.breadth[0], ok1.blocks.movers[0]], ["live", "live", true, true, false, false], "a fresh limited snapshot is LIVE for indices and prices only");
        eq(L.labelOf(ok1), "LIVE", "label LIVE"); }
      for (const [name, r, rx, shift] of [["no Date", { status: 200, text: JSON.stringify(pubDoc(base.doc)) }, /no usable time/], ["null Date", res(pubDoc(base.doc), null), /no usable time/], ["NaN Date", res(pubDoc(base.doc), NaN), /no usable time/],
        ["string Date", res(pubDoc(base.doc), "x"), /no usable time/], ["Date 61 s behind this device", res(pubDoc(base.doc), now), /disagree/, 61000], ["Date 61 s ahead of this device", res(pubDoc(base.doc), now), /disagree/, -61000],
        ["a replayed old response", res(pubDoc(base.doc), now), /disagree/, 600000]]) {
        const v = pubCtl().ingest(r, now + (shift || 0)); eq([v.state, v.blocks, L.labelOf(v)], ["hidden", null, "UNAVAILABLE"], name + ": never LIVE"); ok(rx.test(v.reason), name + ": reason " + v.reason); }
      eq(pubCtl().ingest(res(pubDoc(base.doc), now), now + 59000).state, "live", "this device 59 s ahead of the service is within the tolerance"); eq(pubCtl().ingest(res(pubDoc(base.doc), now), now - 59000).state, "live", "this device 59 s behind is within the tolerance");
      { const f = clone(pubDoc(base.doc)); f.server_ts_ms = T0 - 24000; f.generated_at = isoOf(f.server_ts_ms);
        const v = pubCtl().ingest(res(f, now), now); eq([v.state, v.kind, L.labelOf(v)], ["hidden", "stale", "STALE"], "a 25 s old snapshot is STALE, not LIVE"); }
      for (const [name, mut] of [["full universe", (d) => { d.scope.universe = "nse_eq_all"; }], ["nifty500", (d) => { d.scope.universe = "nifty500"; }],
        ["breadth counted", (d) => { d.breadth = { advances: 1, declines: 0, unchanged: 0, counted: 1 }; }], ["gainers present", (d) => { d.gainers = []; d.losers = []; }]]) {
        const d = pubDoc(base.doc); mut(d); const v = pubCtl().ingest(res(d, now), now); eq([v.state, v.blocks, L.labelOf(v)], ["hidden", null, "UNAVAILABLE"], name + ": the public feed carries only the limited set"); }
      for (const [name, mut] of [["quality not ok", (d) => { d.quality = { ok: false, reasons: ["feed_silent"], warnings: [] }; }], ["market closed", (d) => { d.market.status = "closed"; }], ["bad schema", (d) => { d.schema = 2; }],
        ["missing key", (d) => { delete d.indices; }]]) {
        const d = pubDoc(base.doc); mut(d); const v = pubCtl().ingest(res(d, now), now); eq(v.state === "live", false, name + ": not live"); eq(v.blocks, null, name + ": no blocks"); }
      { const c = pubCtl(); c.ingest(res(pubDoc(base.doc), now), now);
        const f = c.ingest({ status: 503, error: "HTTP 503" }, now + 5000); eq([f.state, f.kind, L.labelOf(f)], ["unavailable", "stale", "STALE"], "a refused poll after a good one is STALE");
        const gone = c.ingest({ status: 503, error: "HTTP 503" }, now + 1000000); eq([gone.kind, L.labelOf(gone)], ["unavailable", "UNAVAILABLE"], "after 15 minutes it is UNAVAILABLE");
        eq(c.ingest({ status: 200, text: "{}", dateMs: now + 6000 }, now + 6000).state, "hidden", "garbage is hidden");
        eq(pubCtl().ingest({ status: 200, text: "x".repeat(70000), dateMs: now }, now).state, "hidden", "an oversize body is refused"); }
      { const c = pubCtl(); c.ingest(res(pubDoc(base.doc), now), now); const older = pubDoc(base.doc); older.server_ts_ms -= 1000; older.generated_at = isoOf(older.server_ts_ms);
        eq(c.ingest(res(older, now), now).state, "hidden", "a snapshot older than one already seen is refused"); }
      eq(new L.Controller({ privateTest: true }).ingest(res(pubDoc(base.doc), undefined), now).state, "live", "the local private-test behaviour is unchanged (no Date requirement)");

      /* rendering */
      { const doc = makeDoc(), ui = new L.Ui(doc, true, true), text = () => doc.byId["market"].textContent, panels = () => doc.byId["market"].all((n) => n.attrs["data-lv"]).map((n) => n.attrs["data-lv"]);
        const eod = () => ["mk-today", "mk-breadth", "mk-movers"].map((id) => doc.byId[id].querySelector("[data-mk]").textContent), before = eod(), c = pubCtl();
        ui.mount(); ok(/^LIVE TEST: connecting/.test(doc.byId["market"].firstChild.textContent), "initial chip");
        ui.update(c.ingest(res(pubDoc(base.doc), now), now));
        eq(panels(), ["public-test"], "one separate limited panel");
        ok(/^LIVE TEST · LIVE · updated/.test(doc.byId["market"].firstChild.textContent), "chip: LIVE TEST · LIVE");
        ok(/A FEW INSTRUMENTS ONLY — not a market dashboard/.test(text()) && /not the market/.test(text()), "labelled as a few instruments, not the market");
        const HEADING = "LIVE TEST — A FEW INSTRUMENTS ONLY — not a market dashboard", heads = () => doc.byId["market"].all((n) => n.className === "lv-head").map((n) => n.textContent);
        eq(heads().length, 1, "one panel heading (LIVE)"); ok(heads()[0].includes(HEADING), "LIVE heading carries the exact required wording: " + heads()[0]);
        ok(!/LOCAL|PRIVATE|Advances|Live gainers|Live losers/.test(text()), "no local wording, no breadth, no rankings");
        eq(doc.byId["mk-today"].all((n) => n.attrs["data-lv"]).length + doc.byId["mk-breadth"].all((n) => n.attrs["data-lv"]).length + doc.byId["mk-movers"].all((n) => n.attrs["data-lv"]).length, 0, "nothing inside the Market Today cards");
        eq(eod(), before, "end-of-day content untouched");
        ui.update(c.ingest({ status: 503, error: "HTTP 503" }, now + 3000)); ok(/STALE/.test(text()) && /not current/.test(text()) && !/· LIVE ·/.test(doc.byId["market"].firstChild.textContent), "stale: labelled STALE, never LIVE");
        eq(heads().length, 1, "one panel heading (STALE)"); ok(heads()[0].includes(HEADING) && /^STALE/.test(heads()[0]), "STALE heading carries the exact required wording: " + heads()[0]);
        ui.update(pubCtl().ingest({ error: "request failed" }, now)); eq(panels(), [], "unavailable: panel removed"); ok(/UNAVAILABLE/.test(text()) && /live service is not reachable/.test(text()) && !/local relay/.test(text()), "unavailable text");
        ui.update(pubCtl().ingest({ status: 200, text: JSON.stringify(pubDoc(base.doc)) }, now)); eq(panels(), [], "no Date: panel removed"); ok(/UNAVAILABLE/.test(doc.byId["market"].firstChild.textContent) && !/ LIVE ·/.test(doc.byId["market"].firstChild.textContent.replace("LIVE TEST", "")), "no Date: chip says UNAVAILABLE");
        eq(eod(), before, "end-of-day content still untouched"); }
      { const evil = pubDoc(base.doc); evil.indices[0].name = "<img src=x onerror=alert(1)>"; const doc = makeDoc(), ui = new L.Ui(doc, true, true);
        ui.update(pubCtl().ingest(res(evil, now), now)); ok(doc.byId["market"].textContent.includes("<img src=x") && doc.byId["market"].all((n) => n.tag === "img").length === 0, "markup from the feed stays text"); }
      eq(JSON.parse(JSON.stringify(new L.Ui(makeDoc(), true).pub)), false, "a local Ui is not public");

      /* start(): the request, the interval, hidden tabs, and a real Date header against the real clock */
      { const doc = makeDoc(), calls = [], timers = [], listeners = {}; doc.addEventListener = (t, f) => { listeners[t] = f; };
        const live = pubDoc(base.doc), t = Date.now() - 1000; live.server_ts_ms = t; live.generated_at = isoOf(t);
        const fetchImpl = (u, o) => { calls.push([u, o]); return Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date().toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(live)) }); };
        const r = load(page(), fetchImpl), out = r.L.start({ url: SVC, pub: true, privateTest: true, port: null }, { document: doc, setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; } });
        const flush = () => new Promise((rs) => setImmediate(rs)); for (let i = 0; i < 4; i++) await flush();
        eq(r.fetches.length, 1, "one request"); eq(r.fetches[0][0], SVC, "the configured address, nothing else");
        eq([r.fetches[0][1].credentials, r.fetches[0][1].mode, r.fetches[0][1].redirect, r.fetches[0][1].cache, r.fetches[0][1].referrerPolicy], ["omit", "cors", "error", "no-store", "no-referrer"], "request options pinned");
        eq(timers[0][1], 10000, "polls every 10 s");
        ok(/^LIVE TEST · LIVE/.test(doc.byId["market"].firstChild.textContent), "a snapshot from this moment, with a matching Date header, is shown as LIVE");
        doc.visibilityState = "hidden"; timers[0][0](); await flush(); eq(r.fetches.length, 1, "a hidden tab makes no request"); eq(timers[timers.length - 1][1], 10000, "and re-arms"); }
      { const doc = makeDoc(), live = pubDoc(base.doc);                                    /* the 2026-10-08 fixture against today's real clock: a Date header from the past cannot make it live */
        const r = load(page(), () => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(T0).toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(live)) }));
        r.L.start({ url: SVC, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 4; i++) await new Promise((rs) => setImmediate(rs));
        ok(/UNAVAILABLE/.test(doc.byId["market"].firstChild.textContent) && !/· LIVE ·/.test(doc.byId["market"].firstChild.textContent), "an old Date header against this device's clock is UNAVAILABLE"); }
      { const doc = makeDoc(), live = pubDoc(base.doc), t = Date.now() - 1000; live.server_ts_ms = t; live.generated_at = isoOf(t);
        const r = load(page(), () => Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(live)) }));
        r.L.start({ url: SVC, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 4; i++) await new Promise((rs) => setImmediate(rs));
        ok(/UNAVAILABLE/.test(doc.byId["market"].firstChild.textContent), "a response with no readable Date header is UNAVAILABLE even if the snapshot looks fresh"); }
    }
    /* ============ Stage 5: the opt-in PUBLIC TEST (?live=public_test). Off for everybody who does not open that exact address. ============ */
    {
      const L = load(loc("example.com", "")).L, base = FIX.cases.find((c) => c.name === "test_set universe"), clone = (d) => JSON.parse(JSON.stringify(d));
      const URL_ = "https://stocklens-live.onrender.com/v1/live/snapshot.json";
      const page = (over) => Object.assign({ protocol: "https:", hostname: "arnavdev11.github.io", port: "", pathname: "/stock/", search: "?live=public_test" }, over || {});
      const got = (l, pub) => { const g = L.gate(l, pub); return g === null ? null : JSON.parse(JSON.stringify(g)); };
      eq(L.PUBLIC_TEST_ORIGIN, "https://stocklens-live.onrender.com", "the fixed hosted origin");
      eq(JSON.parse(JSON.stringify(L.PUBLIC_LIVE)), { enabled: false, url: "" }, "the production switch is still OFF");
      /* opted in */
      eq(got(page()), { url: URL_, pub: true, privateTest: true, port: null, optIn: true }, "the exact page + ?live=public_test -> the fixed service address, limited public mode");
      for (const p of ["/stock/", "/stock", "/stock/index.html"]) eq(got(page({ pathname: p })) !== null, true, "page path " + p);
      eq(got(page({ port: undefined })) !== null, true, "an absent port is the default port");
      eq(got(page(), { enabled: false, url: "" }) !== null, true, "opt-in works with the production switch off (and does not turn it on)");
      eq(JSON.parse(JSON.stringify(L.PUBLIC_LIVE)).enabled, false, "still off afterwards");
      /* everything else on the real page: nothing */
      for (const s of ["", "?", "?live=local", "?live=public", "?live=Public_Test", "?live=public_test ", "?live=public_test2", "?live=public_testing", "?live_set=private_test", "?live_set=public_test", "?public_test=1",
        "?live=public_test&live=local", "?live=local&live=public_test", "?live=", "?x=live=public_test", "#live=public_test", "?live[]=public_test"])
        eq(got(page({ search: s })), null, "no opt-in for search " + JSON.stringify(s));
      eq(got(page({ search: "?live=public_test&live_set=private_test&url=https://evil.example/v1/live/snapshot.json&origin=https://evil.example" })).url, URL_, "extra options cannot change the address");
      eq(got(page({ search: "?foo=1&live=public_test" })) !== null, true, "an unrelated option beside it is ignored");
      /* only the exact host / scheme / port / path */
      for (const l of [page({ protocol: "http:" }), page({ protocol: "file:" }), page({ protocol: undefined }), page({ hostname: "arnavdev11.github.io." }), page({ hostname: "ARNAVDEV11.github.io" }), page({ hostname: "evil.github.io" }),
        page({ hostname: "arnavdev11.github.io.evil.com" }), page({ hostname: "evil-arnavdev11.github.io" }), page({ hostname: "xarnavdev11.github.io" }), page({ hostname: "stocklens-live.onrender.com" }),
        page({ hostname: "example.com" }), page({ hostname: "" }), page({ hostname: 5 }), page({ port: "8443" }), page({ port: "443" }), page({ pathname: "/other/" }), page({ pathname: "/" }), page({ pathname: "/stock/../other/" }),
        page({ pathname: "/stocks/" }), page({ pathname: "/stock/x" }), page({ pathname: 5 }), null, undefined, {}, { hostname: "arnavdev11.github.io" }])
        eq(got(l), null, "no opt-in from page " + JSON.stringify(l));
      eq(got({ protocol: "https:", hostname: "arnavdev11.github.io", pathname: "/stock/", search: { toString() { throw new Error("x"); } } }), null, "a hostile search value is refused, not thrown");
      /* loopback pages keep their own behaviour */
      eq(L.gate(loc("127.0.0.1", "?live=public_test")), null, "a loopback page never takes the public test");
      eq(L.gate(loc("localhost", "?live=public_test&live_set=private_test")), null, "nor localhost");
      eq(L.gate(loc("127.0.0.1", "?live=local")).port, 8765, "the local gate is unchanged");
      eq(L.gate(loc("127.0.0.1", "?live=local&live_set=private_test")).privateTest, true, "the local private test is unchanged");
      /* the real page: nothing happens without the option */
      for (const l of [page({ search: "" }), page({ search: "?live=local" }), page({ search: "?live=public" }), page({ search: "?live_set=private_test" })]) {
        const r = load(l); eq(r.fetches.length, 0, "no request"); eq(r.doc.calls.create, 0, "no DOM node"); eq(r.timers.length, 0, "no timer"); }
      /* with the option: exactly one fetch of the fixed address, with the strict fetch options */
      { const r = load(page()); eq(r.fetches.length, 1, "one request on load");
        eq(r.fetches[0][0], URL_, "the fixed address");
        const o = r.fetches[0][1]; eq([o.cache, o.credentials, o.mode, o.redirect, o.referrerPolicy], ["no-store", "omit", "cors", "error", "no-referrer"], "no cache, no credentials, no redirects"); }
      /* the controller in this mode is the public one: the service's clock is required, only the limited set is accepted */
      { const pubDoc = (d) => { const x = clone(d); x.breadth = { advances: 0, declines: 0, unchanged: 0, counted: 0 }; x.gainers = null; x.losers = null; return x; };
        const now = base.doc.server_ts_ms + 1000, ctl = () => new L.Controller({ privateTest: true, pub: true });
        eq(ctl().ingest({ status: 200, text: JSON.stringify(pubDoc(base.doc)), dateMs: now }, now).state, "live", "a fresh limited snapshot is live");
        eq(ctl().ingest({ status: 200, text: JSON.stringify(pubDoc(base.doc)) }, now).state, "hidden", "no service clock -> never live");
        const full = pubDoc(base.doc); full.scope.universe = "nse_eq_all";
        eq(ctl().ingest({ status: 200, text: JSON.stringify(full), dateMs: now }, now).state, "hidden", "a full-universe snapshot is refused in the public test");
        for (const bad of [{ status: 503, error: "HTTP 503" }, { status: 404, error: "HTTP 404" }, { error: "request failed" }]) {
          const v = ctl().ingest(bad, now); eq([v.state, L.labelOf(v)], ["unavailable", "UNAVAILABLE"], "a disabled / missing / unreachable service is UNAVAILABLE, never LIVE"); } }
      /* the source adds no other way in */
      ok(!/live_origin|live_url|q\.get\(["'](url|origin|backend)/.test(code), "no option names an address or backend");
      ok((code.match(/PUBLIC_TEST_ORIGIN\+PATH_LIVE/g) || []).length === 1, "the address is built in exactly one place, from two constants");
      /* ---- SYNTHETIC off-hours fixtures: made-up service answers for testing the page's messages. They contain NO market data, are never served by index.html, and prove nothing about the real feed. ---- */
      { const SYNTHETIC = "SYNTHETIC TEST FIXTURE - not a real service answer";
        const answer = (status, body, hdr) => () => Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: (k) => (hdr || {})[k.toLowerCase()] || null }, text: () => Promise.resolve(body) });
        const cases = [
          ["snapshot_disabled", 503, /switched off/], ["no_snapshot_yet", 503, /not produced a snapshot yet.*market hours/], ["feed_unavailable", 503, /no working exchange feed/],
          ["stale_feed", 503, /stopped updating/], ["invalid_snapshot", 503, /failed its own checks/]];
        for (const [code, st, rx] of cases) {
          const doc = makeDoc(), r = load(page(), answer(st, JSON.stringify({ error: code, note: SYNTHETIC })));
          r.L.start({ url: URL_, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 6; i++) await new Promise((rs) => setImmediate(rs));
          const chip = doc.byId["market"].firstChild.textContent;
          ok(/UNAVAILABLE/.test(chip) && rx.test(chip) && !/· LIVE ·/.test(chip), "specific unavailable message for " + code + ": " + chip);
          eq(doc.byId["market"].all((n) => n.attrs["data-lv"]).length, 0, "no live panel for " + code); }
        /* unknown codes, junk and oversize bodies fall back to the generic message and never leak the body text */
        for (const body of [JSON.stringify({ error: "<img src=x>" }), "not json", JSON.stringify({ error: 5 }), JSON.stringify({ error: "constructor" }), JSON.stringify({ error: "x".repeat(70000) })]) {
          const doc = makeDoc(), r = load(page(), answer(503, body));
          r.L.start({ url: URL_, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 6; i++) await new Promise((rs) => setImmediate(rs));
          const chip = doc.byId["market"].firstChild.textContent; ok(/UNAVAILABLE/.test(chip) && /not reachable/.test(chip) && !/<img|constructor/.test(chip), "generic message for a bad error body"); }
        { const doc = makeDoc(), r = load(page(), answer(503, "{}", { "content-length": "999999" }));
          r.L.start({ url: URL_, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 6; i++) await new Promise((rs) => setImmediate(rs));
          ok(/not reachable/.test(doc.byId["market"].firstChild.textContent), "an oversize refusal is not read"); }
        /* an unreachable / CORS-blocked service */
        { const doc = makeDoc(), r = load(page(), () => Promise.reject(new TypeError("Failed to fetch")));
          r.L.start({ url: URL_, pub: true, privateTest: true, port: null }, { document: doc, setTimeout() {} }); for (let i = 0; i < 6; i++) await new Promise((rs) => setImmediate(rs));
          ok(/UNAVAILABLE/.test(doc.byId["market"].firstChild.textContent), "unreachable -> UNAVAILABLE"); }
        /* a code on the LOCAL (non-public) path changes nothing: local wording is untouched */
        { const c = new L.Controller({ privateTest: true, pub: false }), v = c.ingest({ status: 503, error: "HTTP 503", code: "no_snapshot_yet" }, Date.now()); eq(v.reason, "HTTP 503", "local mode ignores service codes"); }
      }
    }
    console.log("Live client tests passed (" + checks + " checks)");
  })();
}
