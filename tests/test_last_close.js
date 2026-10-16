// Run: node tests/test_last_close.js   (no network, no browser; stubs the DOM, location and fetch)
// Tests the all-stock dated "Last close" on the Stock Detail page: source out/market_dma.json (NSE end-of-day), the historical-candle fallback, date labels,
// missing/invalid data, exact symbol matching and Market Cap. Read-only: nothing here calls a provider.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const blocks = html.split("<script>").slice(1).map((b) => b.split("</script>")[0]);
const code = blocks.find((b) => b.includes("Stock Detail view"));
assert.ok(code, "detail script block exists");
let checks = 0;
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); }, ok = (c, m) => { checks++; assert.ok(c, m); },
      re = (s, r, m) => { checks++; assert.match(s, r, m); }, no = (s, r, m) => { checks++; assert.doesNotMatch(s, r, m); };

const DAY = "2026-10-15";
const EOD = (stocks, o = {}) => Object.assign({ schema_version: 1, kind: "market_dma", as_of: DAY, freshness: "EOD", universe: "NSE EQ series", stocks }, o);
const S = (symbol, close) => ({ symbol, close, liquid: true, dma: {} });
const FULL = EOD([S("TCS", 2350), S("RELIANCE", 1420.55), S("INFY", 1510.1), S("HDFCBANK", 1650.75), S("ITC", 410.2), S("20MICRONS", 215.35), S("ZEROCLOSE", 0), S("NEGCLOSE", -4),
                  S("NULLCLOSE", null), S("STRCLOSE", "99.5"), S("NANCLOSE", NaN), S("INFCLOSE", Infinity), S("DUP", 10), S("DUP", 11), S("SAMEDUP", 7), S("SAMEDUP", 7)]);
const C = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
const Q = (quarter_end, status, shares) => ({ quarter_end, status, diagnostics: shares === undefined ? null : { share_basis_total: shares } });
const SH = { schema: 1, stocks: { TCS: { symbol: "TCS", quarters: [Q("2026-06-30", "available", 3618087518)] }, "20MICRONS": { symbol: "20MICRONS", quarters: [Q("2026-06-30", "available", 35000000)] },
  ITC: { symbol: "ITC", quarters: [Q("2026-06-30", "unavailable", 5)] } } };
const VAL = { as_of: "2026-10-01", source: "Upstox", stocks: [{ symbol: "TCS", company_name: "TATA CONSULTANCY SERVICES LTD" }] };

async function boot(hash, files) {
  const els = {}, listeners = {}, win = {}, classes = new Set(), fetched = [];
  const mk = (id) => ({ id, innerHTML: "", textContent: "" });
  global.window = { location: { hash }, addEventListener: (t, f) => { win[t] = f; }, scrollTo() {} };
  global.document = { body: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } },
    getElementById: (i) => els[i] || (els[i] = mk(i)), addEventListener: (t, f) => { listeners[t] = f; } };
  global.fetch = async (u) => { fetched.push(u); const f = files[u.split("/").pop()]; return { ok: !!f, json: async () => JSON.parse(JSON.stringify(f, (k, v) => (typeof v === "number" && !isFinite(v) ? { __n: String(v) } : v)), (k, v) => (v && v.__n ? Number(v.__n) : v)) }; };
  eval(code);
  await new Promise((r) => setTimeout(r, 30));
  return { fetched, detail: () => els.detail.innerHTML };
}
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tile = (h, label) => text((h.match(new RegExp('<div class="tile"><b[^>]*>([^<]*)</b><span>' + label.replace(/[()/]/g, "\\$&") + "</span>")) || [])[1] || "NOT FOUND");
const PRICE = "Latest Price (last close, ₹)", CAP = "Market Cap (₹ crore)", CHG = "Day change vs previous close";
const page = async (sym, files) => { const t = await boot("#stock=" + sym, Object.assign({ "fundamentals.json": VAL, "shareholding.json": SH, "market_dma.json": FULL }, files)); const d = t.detail();
  return { d, p: tile(d, PRICE), c: tile(d, CHG), m: tile(d, CAP), note: (d.match(/<p class="note"[^>]*data-last-close[^>]*>([\s\S]*?)<\/p>/) || [])[1] || "", fetched: t.fetched }; };

(async () => {
  // --- representative stocks come from the all-stock file with NO historical shard at all
  let r = await page("TCS", {});
  eq(r.p, "2,350.00", "TCS close from market_dma.json, with no historical.json"); re(text(r.note), /^Last close — 15 Oct 2026 · NSE end-of-day data · not a live price\./, "dated label uses the SOURCE date");
  eq(r.c, "-", "no previous close on file: the day change is a dash, never 0"); re(text(r.note), /Day change shows "-"/, "and the note says why");
  eq(r.m, new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(2350 * 3618087518 / 1e7), "market cap = valid close x NSE-filed share count");
  for (const [s, px] of [["RELIANCE", "1,420.55"], ["INFY", "1,510.10"], ["HDFCBANK", "1,650.75"], ["ITC", "410.20"], ["20MICRONS", "215.35"]]) { r = await page(s, {}); eq(r.p, px, s + " close"); re(text(r.note), /Last close — 15 Oct 2026/, s + " dated"); no(r.d, /\blive\b(?! (price|value))/i, s + ": never called live"); }
  r = await page("20MICRONS", {}); eq(r.m, new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(215.35 * 35000000 / 1e7), "smaller-cap market cap");
  ok(r.fetched.every((u) => /^out\/[a-z_]+\.json$/.test(u)), "only static out/ files are requested (no provider, no token, no live feed)");
  ok(r.fetched.includes("out/market_dma.json"), "the all-stock file is requested");

  // --- the date is the file's date, never today's; an old file is flagged, not hidden
  r = await page("TCS", { "market_dma.json": EOD([S("TCS", 100)], { as_of: "2026-09-01" }) }); eq(r.p, "100.00", "an old close is still shown"); re(text(r.note), /Last close — 01 Sep 2026/, "with ITS date"); re(text(r.note), /days old and may be out of date/, "and flagged as old");
  { const n = new Date(), lab = String(n.getUTCDate()).padStart(2, "0") + " " + ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][n.getUTCMonth()] + " " + n.getUTCFullYear(); no(text(r.note), new RegExp("Last close — " + lab), "today's date is never used as the close date"); }
  r = await page("TCS", { "market_dma.json": EOD([S("TCS", 100)], { as_of: new Date(Date.now() - 864e5).toISOString().slice(0, 10) }) }); no(text(r.note), /may be out of date/, "yesterday's close is not flagged");

  // --- missing / invalid data: Unavailable, never zero, never a substitute
  for (const s of ["NOSUCH", "ZEROCLOSE", "NEGCLOSE", "NULLCLOSE", "STRCLOSE", "NANCLOSE", "INFCLOSE", "DUP"]) {
    r = await page(s, {}); eq(r.p, "Unavailable", s + ": no valid close"); eq(r.m, "Unavailable", s + ": so no market cap"); re(text(r.note), /^Last close — Unavailable/, s + ": the note says unavailable"); no(r.d, /0\.00|NaN|undefined|null|Infinity/, s + ": no fake number");
  }
  r = await page("SAMEDUP", {}); eq(r.p, "7.00", "an identical repeated entry is not ambiguous");
  for (const bad of ["tcs", "Tcs", " TCS", "TCS "]) { r = await page("TCS", { "market_dma.json": EOD([S(bad, 1)]) }); eq(r.p, "Unavailable", "symbol must match exactly: " + JSON.stringify(bad)); }
  r = await page("CONSTRUCTOR", { "market_dma.json": EOD([S("TCS", 1)]) }); eq(r.p, "Unavailable", "a name like an Object property is just an unknown symbol");
  r = await page("TCS", { "market_dma.json": EOD([null, 5, "x", {}, { symbol: 7, close: 1 }, S("TCS", 2350)]) }); eq(r.p, "2,350.00", "malformed entries are skipped, valid ones kept");

  // --- invalid document, date or shape: ignored (falls back), never trusted
  const H1 = { stocks: { TCS: { candles: [C("2026-10-14", 2300), C("2026-10-13", 2200)] } } };
  for (const [name, doc] of [["wrong kind", EOD([S("TCS", 5)], { kind: "scans" })], ["no kind", EOD([S("TCS", 5)], { kind: undefined })], ["bad date", EOD([S("TCS", 5)], { as_of: "2026-13-45" })], ["text date", EOD([S("TCS", 5)], { as_of: "yesterday" })],
    ["missing date", EOD([S("TCS", 5)], { as_of: undefined })], ["future date", EOD([S("TCS", 5)], { as_of: "2099-01-01" })], ["stocks not an array", EOD({ TCS: 5 })], ["stocks missing", EOD(undefined)], ["not an object", 7]]) {
    r = await page("TCS", { "market_dma.json": doc, "historical.json": H1 }); eq(r.p, "2,300.00", name + ": falls back to the historical candle"); re(text(r.note), /Last close — 14 Oct 2026 · daily candle in the historical data/, name + ": labelled with the candle's own source and date"); no(r.d, /5\.00/, name + ": the bad file's value is never shown");
    r = await page("TCS", { "market_dma.json": doc }); eq(r.p, "Unavailable", name + ": and without history there is no price");
  }
  r = await page("TCS", { "market_dma.json": undefined, "historical.json": H1 }); eq([r.p, r.c], ["2,300.00", "+4.55%"], "file missing (404): exactly the historical behaviour, including the day change"); re(text(r.note), /Last close — 14 Oct 2026 · daily candle/, "and still dated");

  // --- two sources: the newer date wins; the day change needs a same-date candle that agrees
  r = await page("TCS", { "historical.json": { stocks: { TCS: { candles: [C("2026-10-15", 2350), C("2026-10-14", 2305.9)] } } } }); eq([r.p, r.c], ["2,350.00", "+1.91%"], "same date, agreeing closes: NSE close with the candle's day change"); re(text(r.note), /NSE end-of-day data/, "source stated");
  r = await page("TCS", { "historical.json": { stocks: { TCS: { candles: [C("2026-10-15", 2351.5), C("2026-10-14", 2305.9)] } } } }); eq([r.p, r.c], ["2,350.00", "-"], "same date, closes disagree: no day change is invented");
  r = await page("TCS", { "historical.json": H1 }); eq([r.p, r.c], ["2,350.00", "-"], "NSE close is a day newer than the last candle: its change is not guessed");
  r = await page("TCS", { "historical.json": { stocks: { TCS: { candles: [C("2026-10-16", 2400), C("2026-10-15", 2350)] } } } }); eq([r.p, r.c], ["2,400.00", "+2.13%"], "a newer candle than the NSE file wins (the file is stale), with its change"); re(text(r.note), /Last close — 16 Oct 2026 · daily candle/, "dated from the candle");
  r = await page("TCS", { "historical.json": { stocks: { TCS: { candles: [C("2026-10-15", 2350)] } } } }); eq(r.p, "2,350.00", "one candle is not needed when the NSE close exists");

  // --- Market Cap: only a valid price AND a share count
  r = await page("ITC", {}); eq([r.p, r.m], ["410.20", "Unavailable"], "valid close but the only quarter is unavailable: no market cap"); re(text(r.d), /Market Cap is Unavailable unless both/, "and the note explains");
  r = await page("RELIANCE", {}); eq([r.p, r.m], ["1,420.55", "Unavailable"], "valid close but no share count on file");
  r = await page("TCS", { "shareholding.json": undefined }); eq([r.p, r.m], ["2,350.00", "Unavailable"], "shareholding file missing");
  r = await page("ZEROCLOSE", { "shareholding.json": { schema: 1, stocks: { ZEROCLOSE: { quarters: [Q("2026-06-30", "available", 1e9)] } } } }); eq(r.m, "Unavailable", "valid share count but invalid close: no market cap");
  r = await page("TCS", {}); re(text(r.d), /Market Cap = last close × 3,61,80,87,518 shares \(NSE shareholding filing, quarter ended 2026-06-30\), not a live value\./, "market cap note names the share basis");

  // --- company identity is untouched: the name comes from fundamentals, never from the price file
  r = await page("TCS", {}); eq(tile(r.d, "Company"), "TATA CONSULTANCY SERVICES LTD", "company name from fundamentals.json"); r = await page("20MICRONS", {}); eq(tile(r.d, "Company"), "-", "no name on file: a dash, never a guess");

  // --- the page source itself
  const mod = code.slice(code.indexOf("function loadData"), code.indexOf("function view"));
  no(mod, /upstox|api\.|wss?:|token|Authorization/i, "no provider URL, socket or credential in the price code"); re(mod, /out\/market_dma\.json/, "reads the published all-stock file only");
  console.log("Last close tests passed (" + checks + " checks)");
})().catch((e) => { console.error("LAST CLOSE TEST FAILED:", e && e.message || e, "\n", e && e.stack ? e.stack.split("\n").slice(1, 4).join("\n") : ""); process.exit(1); });
