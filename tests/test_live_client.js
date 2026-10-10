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
  const evil = JSON.parse(JSON.stringify(good.doc)); evil.indices[0].name = "<img src=x onerror=alert(1)>";
  const doc2 = makeDoc(), ui2 = new L.Ui(doc2); ui2.update({ state: "live", reason: "", blocks: { indices: [true, ""], breadth: [false, "x"], movers: [false, "x"], prices: [true, ""] }, doc: evil });
  ok(doc2.byId["market"].textContent.includes("<img src=x onerror=alert(1)>"), "markup is shown as text"); ok(doc2.byId["market"].all((n) => n.tag === "img").length === 0, "no element is created from it");
  /* failure removes every panel */
  ui.update(ctl.ingest({ error: "request failed" }, now)); eq(panels(), [], "panels removed when the relay fails"); ok(/not reachable/.test(doc.byId["market"].firstChild.textContent), "chip says so"); eq(eodText(), before, "EOD still intact");
  /* test_set / stale: hidden with reason */
  const ts = FIX.cases.find((c) => c.doc && c.doc.scope && c.doc.scope.universe === "test_set");
  const ui3 = new L.Ui(makeDoc()); ui3.update(new L.Controller().ingest({ status: 200, text: JSON.stringify(ts.doc), dateMs: ts.now }, ts.now)); eq(ui3.panels.indices || null, null, "no panel for test_set");
}

console.log("Live client tests passed (" + checks + " checks)");
