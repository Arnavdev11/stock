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
ok([...code.matchAll(/https?:\/\/[^\s"'<)]+/g)].every((m) => m[0] === "http://127.0.0.1:"), "the only web address is the loopback relay");
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
    ok(/^Live \(PRIVATE TEST SET\) · LIVE/.test(r.doc.byId["market"].firstChild.textContent), "chip says private test set");
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
    console.log("Live client tests passed (" + checks + " checks)");
  })();
}
