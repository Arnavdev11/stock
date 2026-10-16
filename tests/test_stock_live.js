"use strict";
// Run: node tests/test_stock_live.js   (no network, no browser: both modules run in a vm with a fake DOM, a fake clock and a fake fetch)
// Tests the Stock Detail live strip (module stocklens-stocklive): LIVE / STALE / LAST CLOSE, per-stock checks, fallback, and that it is inert unless the existing live gate is on.
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
let checks = 0;
const ok = (c, m) => { checks++; if (!c) { console.error("STOCK LIVE TEST FAILED: " + m); process.exit(1); } };
const eq = (a, b, m) => { checks++; try { assert.deepStrictEqual(a, b); } catch (e) { console.error("STOCK LIVE TEST FAILED: " + m + "\n" + e.message); process.exit(1); } };
const mod = (id) => { const tag = '<script type="module" id="' + id + '">'; ok(html.split(tag).length === 2, "exactly one " + id); return html.split(tag)[1].split("</script>")[0]; };
const LIVE = mod("stocklens-live"), SL = mod("stocklens-stocklive");

/* ---- static: the module is inert code: no request, no address, no credential, no HTML sink ---- */
{ const c = SL.replace(/\/\*[\s\S]*?\*\//g, "");
  ok(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|importScripts|localStorage|sessionStorage|document\.cookie/.test(c), "no request and no storage");
  ok(!/https?:\/\/|wss?:\/\//.test(c), "no web address");
  ok(!/innerHTML|outerHTML|insertAdjacent|\beval\s*\(|new Function|document\.write/.test(c), "no HTML or code sink");
  ok(!/token|secret|apikey|api_key|bearer|authorization|password|credential|upstox/i.test(c), "no credential or provider word");
  ok(!/\b(buy|sell|target price|recommend)/i.test(c), "no advice language"); }

/* ---- fake DOM ---- */
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parentNode = null; this.className = ""; this._t = ""; this.attrs = {}; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  get nextSibling() { if (!this.parentNode) return null; const s = this.parentNode.children; return s[s.indexOf(this) + 1] || null; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  querySelector(sel) { const m = /^\[([a-z-]+)\]$/.exec(sel); const f = (n) => { for (const c of n.children) { if (m && c.attrs[m[1]] !== undefined) return c; const r = f(c); if (r) return r; } return null; }; return f(this); }
}
const T0 = Date.UTC(2026, 9, 16, 6, 45, 0);                   // 16 Oct 2026, 12:15:00 IST
const isoIST = (ms) => new Date(ms + 19800000).toISOString().slice(0, 23) + "+05:30";
const SESSION = "2026-10-16", SVC = "https://stocklens-live.onrender.com/v1/live/snapshot.json";
const entry = (ltp, cp, ageS = 2, over = {}) => Object.assign({ ltp, cp, change_pct: Math.round((ltp / cp - 1) * 100000) / 1000, ltt: isoIST(T0 - ageS * 1000) }, over);
const PRICES = () => ({ RELIANCE: entry(1420.55, 1400), TCS: entry(2428.1, 2354.4), INFY: entry(1510, 1500), HDFCBANK: entry(1650.75, 1640), ITC: entry(410.2, 408) });
const snap = (over = {}, prices) => Object.assign({ schema: 1, kind: "live_snapshot", seq: 9, generated_at: isoIST(T0), server_ts_ms: T0,
  market: { status: "open", session_date: SESSION, segments: { NSE_EQ: "NORMAL_OPEN" }, last_tick_at: isoIST(T0 - 1000) },
  scope: { universe: "test_set", subscribed: 6, ticked: 6, coverage: 1, indices_expected: 1 },
  indices: [{ name: "NIFTY 50", ltp: 24800, cp: 24500, change: 300, change_pct: 1.224, ltt: isoIST(T0 - 1000) }],
  breadth: { advances: 0, declines: 0, unchanged: 0, counted: 0 }, gainers: null, losers: null, prices: prices || PRICES(),
  quality: { ok: true, reasons: [], warnings: [] }, source: "Upstox market data feed V3 (ltpc)" }, over);

/* ---- one world per test: real live module + real strip module, fake clock, fake fetch ---- */
function world(opts = {}) {
  let clock = T0 + 1000; const fetches = [];
  class FDate extends Date { static now() { return clock; } constructor(...a) { if (a.length) super(...a); else super(clock); } }
  const doc = { visibilityState: "visible", byId: {}, head: new El("head"), documentElement: new El("html") };
  doc.createElement = (t) => new El(t); doc.getElementById = (id) => doc.byId[id] || null;
  const detail = new El("div"); doc.byId.detail = detail;
  const note = new El("p"); note.setAttribute("data-last-close", "1"); if (opts.closeDate !== null) note.setAttribute("data-close-date", opts.closeDate || "2026-10-15"); note.textContent = "Last close — 15 Oct 2026";
  const tail = new El("p"); tail.textContent = "after"; detail.appendChild(note); detail.appendChild(tail);
  const listeners = {}, timers = [];
  const win = { location: { protocol: "https:", hostname: "arnavdev11.github.io", port: "", pathname: "/stock/", search: opts.search === undefined ? "?live=public_test" : opts.search, hash: opts.hash || "#stock=TCS" },
    addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); }, dispatchEvent: (e) => { (listeners[e.type] || []).forEach((f) => f(e)); return true; }, setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; } };
  win.window = win;
  let answer = opts.answer || (() => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(clock).toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(snap())) }));
  const ctx = { window: win, document: doc, Date: FDate, TextDecoder, URLSearchParams, JSON, Math, Object, Array, Number, String, isFinite, Intl, encodeURIComponent, CustomEvent: class { constructor(t) { this.type = t; } },
    MutationObserver: undefined, setInterval() { return 1; }, setTimeout: (f, ms) => { timers.push([f, ms]); return 1; }, clearTimeout() {},
    fetch: (u, o) => { fetches.push([u, o]); return answer(u, o); } };
  vm.createContext(ctx); vm.runInContext(LIVE, ctx); vm.runInContext(SL, ctx);
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  return { win, doc, detail, note, fetches, L: win.SLLive, S: win.SLStockLive, flush, setClock: (v) => { clock = v; }, tick: (ms) => { clock += ms; }, setAnswer: (f) => { answer = f; },
    strip: () => detail.querySelector("[data-live-strip]"), poll: async function () { this.L.start && 0; } };
}
const view = (kind, doc, extra = {}) => Object.assign({ state: kind === "live" ? "live" : "hidden", kind, reason: "", blocks: null, doc: kind === "live" ? doc : null, last: kind === "stale" ? doc : null }, extra);
const lastOf = (v, at = T0 + 1000, dateMs = T0 + 1000) => ({ view: v, at, dateMs });
const S = (() => { const w = world(); return w.S; })();
const NOW = T0 + 1000;

(async () => {
  /* ===== fresh data: LIVE ===== */
  let a = S.assess(lastOf(view("live", snap())), "TCS", NOW);
  eq([a.state, a.quote.ltp, a.quote.cp, a.quote.pct, a.quote.time], ["live", 2428.1, 2354.4, 3.13, "12:14:58"], "fresh entry for a symbol in the snapshot is LIVE with its own price, previous close, change and trade time");
  ok(/^LIVE · ₹2,428\.10 · \+3\.13% vs previous close · last trade 12:14:58 IST · snapshot 1 s old · limited test feed$/.test(S.words(a, "15 Oct 2026")), "LIVE wording: price, change, trade time, snapshot age, test-feed note: " + S.words(a, null));
  for (const sym of ["RELIANCE", "INFY", "HDFCBANK", "ITC"]) eq(S.assess(lastOf(view("live", snap())), sym, NOW).state, "live", sym + " is LIVE");
  eq(S.assess(lastOf(view("live", snap({}, { TCS: entry(2428.1, null, 2) }))), "TCS", NOW).quote.pct, null, "no previous close: LIVE price but no change claimed");

  /* ===== age: the snapshot's own clock plus time since it arrived ===== */
  eq(S.assess(lastOf(view("live", snap())), "TCS", NOW + 19000 - 1000 + 1000 - 1000).state, "live", "just inside 20 s is LIVE");
  a = S.assess(lastOf(view("live", snap())), "TCS", NOW + 25000); eq([a.state, a.quote.ltp], ["stale", 2428.1], "25 s after arrival with no newer answer: STALE, never LIVE");
  ok(/^STALE · not current · last live ₹2,428\.10 at 12:14:58 IST \(26 s ago\)/.test(S.words(a, null)), "STALE wording says not current, with its own trade time: " + S.words(a, null));
  eq(S.assess(lastOf(view("live", snap())), "TCS", NOW + 899000).state, "stale", "still STALE within 15 minutes");
  a = S.assess(lastOf(view("live", snap())), "TCS", NOW + 901000); eq([a.state, a.quote], ["last_close", null], "older than 15 minutes: LAST CLOSE");
  eq(S.assess(lastOf(view("live", snap({ server_ts_ms: T0 - 30000 }, { TCS: entry(2428.1, 2354.4, 33) })), T0 + 1000, T0 + 1000), "TCS", NOW).state, "stale", "a 31-second-old snapshot by the service's clock is STALE even though it just arrived");
  eq(S.assess(lastOf(view("live", snap({ server_ts_ms: T0 + 60000 }))), "TCS", NOW).state, "last_close", "a snapshot from the future is refused");
  eq(S.assess(lastOf(view("live", snap()), T0 + 1000, null), "TCS", NOW).state, "live", "without a Date header the device clock is used (the existing live layer decides whether that is allowed)");
  a = S.assess(lastOf(view("live", snap({}, { TCS: entry(2428.1, 2354.4, 200) }))), "TCS", NOW); eq(a.state, "stale", "fresh snapshot but this stock last traded 200 s ago: STALE");
  eq(S.assess(lastOf(view("live", snap({}, { TCS: entry(2428.1, 2354.4, 119) }))), "TCS", NOW).state, "live", "traded 119 s before the snapshot is still LIVE");

  /* ===== stale view from the live layer (a valid older answer) ===== */
  a = S.assess(lastOf(view("stale", snap({ server_ts_ms: T0 - 120000, generated_at: isoIST(T0 - 120000) }, { TCS: entry(2428.1, 2354.4, 130) }))), "TCS", NOW); eq(a.state, "stale", "a stale view with a valid older snapshot is STALE");
  eq(S.assess(lastOf(view("stale", snap({ server_ts_ms: T0 - 1000000 }))), "TCS", NOW).state, "last_close", "a stale view older than 15 minutes is LAST CLOSE");

  /* ===== market closed, no answer, errors: LAST CLOSE with the reason, never a number ===== */
  a = S.assess(lastOf(view("closed", null, { reason: "market is closed" })), "TCS", NOW); eq([a.state, a.quote, a.closed], ["last_close", null, true], "market closed: LAST CLOSE");
  ok(/^LAST CLOSE — 15 Oct 2026 · no live price: market is closed · the dated close above is not a live price$/.test(S.words(a, "15 Oct 2026")), "LAST CLOSE wording carries the dated close: " + S.words(a, "15 Oct 2026"));
  ok(/^LAST CLOSE · no live price/.test(S.words(a, null)), "LAST CLOSE without a date still never says live");
  eq(S.assess(null, "TCS", NOW).state, "last_close", "no answer yet"); eq(S.assess({}, "TCS", NOW).state, "last_close", "empty answer"); eq(S.assess({ view: null, at: 1 }, "TCS", NOW).state, "last_close", "no view");
  eq(S.assess(lastOf(view("unavailable", null, { state: "unavailable", reason: "request failed" })), "TCS", NOW).reason, "request failed", "unavailable keeps the existing plain-words reason");
  eq(S.assess(lastOf(view("hidden", null, { reason: "relay reports a problem: feed_silent" })), "TCS", NOW).state, "last_close", "relay problem: LAST CLOSE");
  eq(S.assess(lastOf({ state: "live", kind: "live", doc: null }), "TCS", NOW).state, "last_close", "live view without a snapshot");
  eq(S.assess(lastOf(view("live", snap())), "", NOW).state, "last_close", "empty symbol"); eq(S.assess(lastOf(view("live", snap())), null, NOW).state, "last_close", "null symbol"); eq(S.assess(lastOf(view("live", snap())), "TCS", "x").state, "last_close", "bad clock");

  /* ===== missing symbols: only symbols present in the snapshot ===== */
  for (const sym of ["SBIN", "NOSUCH", "tcs", "TCS ", "constructor", "__proto__", "toString", "hasOwnProperty"]) { a = S.assess(lastOf(view("live", snap())), sym, NOW); eq([a.state, a.quote, a.notInSet], ["last_close", null, true], "not in the live set: " + JSON.stringify(sym)); }
  eq(S.assess(lastOf(view("live", snap({}, {}))), "TCS", NOW).state, "last_close", "an empty prices block");
  eq(S.assess(lastOf(view("live", snap({ scope: { universe: "test_set", subscribed: 6, ticked: 6, coverage: 1, indices_expected: 1 } }, { RELIANCE: entry(1, 1) }))), "TCS", NOW).state, "last_close", "one symbol present does not make another live");

  /* ===== malformed entries: never LIVE, never a number ===== */
  const bad = {
    "ltp string": entry("2428.1", 2354.4), "ltp zero": entry(0, 2354.4, 2, { change_pct: -100 }), "ltp negative": entry(-1, 2354.4, 2, { change_pct: -100 }), "ltp NaN": entry(NaN, 2354.4), "ltp null": entry(null, 2354.4), "ltp Infinity": entry(Infinity, 2354.4),
    "cp zero": entry(2428.1, 0, 2, { change_pct: 0 }), "cp negative": entry(2428.1, -5, 2, { change_pct: 0 }), "cp string": entry(2428.1, "2354.4", 2, { change_pct: 3.131 }),
    "change_pct mismatch": entry(2428.1, 2354.4, 2, { change_pct: 9.9 }), "change_pct missing": (() => { const e = entry(2428.1, 2354.4); delete e.change_pct; return e; })(), "change_pct string": entry(2428.1, 2354.4, 2, { change_pct: "3.131" }),
    "implausible move": entry(4000, 2354.4), "ltt missing": (() => { const e = entry(2428.1, 2354.4); delete e.ltt; return e; })(), "ltt null": entry(2428.1, 2354.4, 2, { ltt: null }), "ltt number": entry(2428.1, 2354.4, 2, { ltt: T0 }),
    "ltt garbage": entry(2428.1, 2354.4, 2, { ltt: "yesterday" }), "ltt UTC form": entry(2428.1, 2354.4, 2, { ltt: new Date(T0 - 2000).toISOString() }), "ltt impossible date": entry(2428.1, 2354.4, 2, { ltt: "2026-02-31T12:14:58.000+05:30" }),
    "ltt previous day": entry(2428.1, 2354.4, 2, { ltt: "2026-10-15T15:29:59.000+05:30" }), "ltt after snapshot": entry(2428.1, 2354.4, 2, { ltt: isoIST(T0 + 6000) }),
    "entry null": null, "entry array": [1, 2], "entry string": "x", "entry number": 7, "entry empty": {} };
  for (const [name, e] of Object.entries(bad)) { a = S.assess(lastOf(view("live", snap({}, { TCS: e }))), "TCS", NOW); ok(a.state === "last_close" && a.quote === null && a.reason, "malformed (" + name + ") is LAST CLOSE with a reason, got " + a.state); }
  eq(S.assess(lastOf(view("live", snap({}, { TCS: entry(2428.1, 2354.4, 2, { ltt: isoIST(T0 + 5000) }) }))), "TCS", NOW).state, "live", "a trade up to 5 s after the snapshot is within the clock allowance");
  for (const [name, d] of Object.entries({ "prices missing": (() => { const d = snap(); delete d.prices; return d; })(), "prices array": snap({ prices: [] }), "prices null": snap({ prices: null }), "market missing": (() => { const d = snap(); delete d.market; return d; })(),
    "session_date null": snap({ market: { status: "open", session_date: null, segments: {}, last_tick_at: null } }), "server_ts string": snap({ server_ts_ms: "1" }), "server_ts missing": (() => { const d = snap(); delete d.server_ts_ms; return d; })() })) {
    a = S.assess(lastOf(view("live", d)), "TCS", NOW); eq([a.state, a.quote], ["last_close", null], "malformed snapshot (" + name + ")"); }
  eq(S.assess(lastOf(view("live", snap({ market: { status: "open", session_date: "2026-10-15", segments: {}, last_tick_at: null } }))), "TCS", NOW).state, "last_close", "a snapshot whose session date differs from the trade date");

  /* ===== end to end with the real live layer: gated, one poll, strip drawn from its answer ===== */
  { const w = world({ search: "" }); w.L.start; await w.flush(); eq(w.L.active(), false, "no gate: the live layer is not active"); eq(w.fetches.length, 0, "no gate: no request at all"); w.S.refresh(); eq(w.strip(), null, "no gate: no strip is drawn"); eq(w.detail.children.length, 2, "no gate: the page is untouched"); }
  { const w = world({ search: "?live=public_test", hash: "#stock=TCS" }); await w.flush();
    eq(w.L.active(), true, "?live=public_test turns the existing live layer on"); eq(w.fetches.length, 1, "exactly one request, made by the existing live layer"); eq(w.fetches[0][0], SVC, "to its one fixed address");
    eq([w.fetches[0][1].credentials, w.fetches[0][1].redirect, w.fetches[0][1].cache], ["omit", "error", "no-store"], "with the existing hardened options");
    const st = w.strip(); ok(st !== null, "a strip is drawn for TCS"); ok(/^LIVE · ₹2,428\.10 · \+3\.13% vs previous close/.test(st.textContent), "the strip says LIVE with the snapshot's price: " + st.textContent); eq(st.getAttribute("data-live-state"), "live", "state attribute");
    eq(w.note.nextSibling, st, "the strip sits directly under the dated-close note, which stays"); eq(w.note.textContent, "Last close — 15 Oct 2026", "the dated close note is unchanged");
    w.refresh = w.S.refresh; w.S.refresh(); eq(w.detail.children.length, 3, "a second refresh does not add a second strip");
    /* the live answer stops arriving: the next polls fail */
    w.setAnswer(() => Promise.reject(new Error("offline"))); w.tick(30000); w.S.refresh(); ok(/^STALE · not current/.test(w.strip().textContent), "30 s later with no newer answer: STALE: " + w.strip().textContent);
    w.tick(900000); w.S.refresh(); ok(/^LAST CLOSE — 15 Oct 2026 · no live price/.test(w.strip().textContent), "after 15 more minutes: LAST CLOSE with the dated close: " + w.strip().textContent); eq(w.strip().getAttribute("data-live-state"), "last_close", "state attribute follows");
    ok(!/LIVE/.test(w.strip().textContent.replace(/no live price|not a live price/g, "")), "LAST CLOSE never says LIVE"); }
  { const w = world({ hash: "#stock=SBIN" }); await w.flush(); const st = w.strip(); ok(/^LAST CLOSE — 15 Oct 2026 · no live price: this stock is not in the live set/.test(st.textContent), "a stock outside the snapshot shows LAST CLOSE, never a live price: " + st.textContent); }
  { const w = world({ hash: "#stock=TCS", answer: () => Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(snap())) }) }); await w.flush();
    ok(/^LAST CLOSE/.test(w.strip().textContent), "the public feed with no usable Date header is refused by the existing layer: LAST CLOSE: " + w.strip().textContent); }
  { const w = world({ hash: "#stock=TCS", answer: () => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(T0 + 1000).toUTCString() : null) }, text: () => Promise.resolve("{not json") }) }); await w.flush();
    ok(/^LAST CLOSE/.test(w.strip().textContent), "malformed JSON: LAST CLOSE"); }
  { const w = world({ hash: "#stock=TCS", answer: () => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(T0 + 1000).toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(snap({ scope: { universe: "nse_eq_all", subscribed: 6, ticked: 6, coverage: 1, indices_expected: 1 } }))) }) }); await w.flush();
    ok(/^LAST CLOSE/.test(w.strip().textContent), "the public feed may carry only the limited test set: any other universe is refused by the existing layer: " + w.strip().textContent); }
  { const w = world({ hash: "#stock=TCS", answer: () => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(T0 + 1000).toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(snap({ market: { status: "closed", session_date: SESSION, segments: {}, last_tick_at: null } }))) }) }); await w.flush();
    ok(/^LAST CLOSE — 15 Oct 2026 · no live price: market is closed/.test(w.strip().textContent), "market closed: " + w.strip().textContent); }
  { const w = world({ hash: "#stock=TCS", answer: () => Promise.resolve({ ok: true, status: 200, headers: { get: (k) => (k.toLowerCase() === "date" ? new Date(T0 + 1000).toUTCString() : null) }, text: () => Promise.resolve(JSON.stringify(snap({ quality: { ok: false, reasons: ["feed_silent"], warnings: [] } }))) }) }); await w.flush();
    ok(/^LAST CLOSE .*relay reports a problem/.test(w.strip().textContent), "relay quality flag false: " + w.strip().textContent); }
  { const w = world({ hash: "#stock=TCS", closeDate: null }); await w.flush(); eq(w.strip().textContent.startsWith("LIVE"), true, "LIVE does not depend on the dated close being present");
    const w2 = world({ hash: "#stock=SBIN", closeDate: null }); await w2.flush(); ok(/^LAST CLOSE · no live price/.test(w2.strip().textContent), "no dated close on the page: says so by omission, never invents a date"); }
  { const w = world({ hash: "#other" }); await w.flush(); eq(w.strip(), null, "not a stock route: no strip"); }
  { const w = world({ hash: "#stock=<script>" }); await w.flush(); eq(w.strip(), null, "an invalid symbol route: no strip"); }
  console.log("Stock live strip tests passed (" + checks + " checks)");
})().catch((e) => { console.error("STOCK LIVE TEST FAILED:", e && e.stack || e); process.exit(1); });
