"use strict";
// Run: node tests/test_responsive_layout.js   (no network, no browser)
// Static regression checks for the responsive redesign of ../index.html: WCAG AA colour contrast computed from the page's own colour tokens (light and dark),
// the desktop search box's minimum size against its placeholder, the bar's breakpoints, and the agreed browser-check widths. The real rendering is checked
// in a browser with tests/browser_checks.py (not run by CI).
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const css = html.slice(html.indexOf("<style>"), html.indexOf("</style></head>"));
let checks = 0;
const ok = (c, m) => { checks++; assert.ok(c, m); }, eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); };

const vars = (block) => Object.fromEntries([...block.matchAll(/--([a-z]+):(#[0-9A-Fa-f]{3,6})/g)].map((m) => [m[1], m[2]]));
const light = vars((css.match(/:root\{([^}]*)\}/) || [])[1] || ""), dark = vars((css.match(/:root\[data-theme="dark"\]\{([^}]*)\}/) || [])[1] || "");
eq(vars((css.match(/@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-theme="light"\]\)\{([^}]*)\}/) || [])[1] || ""), dark, "the system dark palette equals the explicit dark theme, so both are covered below");
const hex = (h) => { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255); };
const lum = (h) => { const [r, g, b] = hex(h).map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4))); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const card = (t) => (t.card === "#fff" ? "#FFFFFF" : t.card);

// --- colour contrast (WCAG 2.2 AA, 4.5:1 for normal text)
for (const [name, t] of [["light", light], ["dark", dark]]) {
  ok(t.up && t.dn && t.mute && t.ac && t.bg && t.card, name + " theme defines every colour token");
  for (const fg of ["ink", "mute", "up", "dn", "ac"]) for (const bg of ["bg", "card"]) {
    const r = ratio(t[fg], bg === "card" ? card(t) : t[bg]); ok(r >= 4.5, name + ": --" + fg + " on --" + bg + " is " + r.toFixed(2) + ":1 (AA needs 4.5)");
  }
}
eq([light.up, light.dn], ["#0B7A50", "#C23333"], "the light gain/loss colours are the AA ones (the earlier #0E8F5E / #D13B3B were 3.79:1 and 4.40:1 on the page background)");
ok(ratio("#FFFFFF", light.ac) >= 4.5, "light: white text on the accent (selected tab, nav item, button) passes");
ok(ratio("#FFFFFF", dark.ac) < 4.5, "dark: white on the dark accent would fail ...");
ok(ratio("#0F1524", dark.ac) >= 4.5, "... so dark mode uses #0F1524 on the accent (" + ratio("#0F1524", dark.ac).toFixed(2) + ":1)");
for (const sel of [".btn", ".tabs button[aria-selected=true]", ".chips button[aria-pressed=true]", "#globalBar .gb-sections a[aria-current=true]", "button[data-tf][aria-pressed=true]"]) {
  ok(css.includes(':root[data-theme="dark"] ' + sel) && css.includes(':root:not([data-theme="light"]) ' + sel), "dark-mode selected text covers " + sel + " (explicit dark theme and the system setting)");
}
ok(/\{color:#0F1524!important\}\}\n:root\[data-theme="dark"\]/.test(css), "the system-setting rule sets the dark text");
ok(/\na\{color:var\(--ac\)\}#detail details>summary\{min-height:24px;/.test(css), "links default to the accent (the browser's blue was 1.72:1 in dark mode on the company-profile links) and disclosure toggles are 24px targets");
ok(/@container \(max-width:680px\)\{#detailShareholding table\.mh,/.test(html), "major shareholders switch to cards below 680px, so long share counts (e.g. 2,59,54,99,419) never break in a squeezed 18% column");
ok(css.includes("#finHistTable td:first-child{min-width:10.5rem}"), "Financial History's row labels keep 10.5rem on a phone (they wrapped to six lines in 97px); the table already scrolls sideways in its own box");
{ const mod = (html.split("/* Responsive redesign: on a phone the Quarterly Results, Investment Checklist, Financial History and shareholding quarter tables become cards")[1] || "").split("</script>")[0];
  ok(!html.includes('id="stocklens-cards"'), "no extra script element (the page's script count is pinned): the helper lives at the end of the screen script");
  ok(mod && /var SEL="#detailQuarterly table,#detailChecklist table",GRID="#finHistTable,#shTable";/.test(mod), "the stock-page card helper targets only Quarterly Results, the Investment Checklist, Financial History and the shareholding quarter table");
  ok(!/fetch\(|innerHTML|textContent=|\.remove\(|insertBefore|appendChild/.test(mod), "it reads nothing, writes no content and moves nothing: it only adds a class and data-label attributes");
  ok(/GRID="#finHistTable,#shTable"/.test(mod), "Financial History and the shareholding quarter table get grid cards too");
  ok(/@container page \(max-width:679px\)\{\n#finHistTable\.sl-cardg\{min-width:0!important;width:100%\}/.test(css), "Financial History (635px, five years) becomes cards below a 680px page");
  ok(/#shTable\.sl-cardg\{min-width:0!important;width:100%\}/.test(css) && /#shTable\.sl-cardg \.sh-subin\{position:static;width:auto;max-width:none\}/.test(css), "shareholding quarters are cards below a 600px page; each Source note returns to the flow inside its card");
  ok(/#shTable\.sl-cardg tr:has\(\+tr\.sh-sub\)\{margin-bottom:0;border-bottom:0;border-radius:10px 10px 0 0\}/.test(css), "a quarter's Source note joins its card (browsers without :has() show it as its own strip)");
  ok(/observe\(d,\{childList:true,subtree:true\}\)/.test(mod), "it relabels when those modules redraw");
  ok(/@container page \(max-width:599px\)\{\ntable\.sl-cardt\{min-width:0!important;width:100%\}/.test(css), "below a 600px page those tables are cards (no column starts off-screen)");
  ok(/table\.sl-cardt td:before\{content:attr\(data-label\)/.test(css) && /#detailChecklist table\.sl-cardt td:empty\{display:none\}/.test(css), "every value keeps its column name; empty notes take no space");
  ok(html.indexOf("Responsive redesign: on a phone the Quarterly Results, Investment Checklist") < html.lastIndexOf("<script>"), "it is not in the last classic script block (tests read that block as the Phase 2B tabs)"); }
ok(html.includes("#compareView th a{display:inline-block;min-height:24px;min-width:24px;padding:4px 0}"), "the comparison table's stock links are 24px targets (in the compare module's own CSS)");
ok(/#detailCompare a\{[^}]*color:var\(--ac\)/.test(css), "the stock page's Compare link uses the accent (the browser's default link blue was 1.94:1 in dark mode)");

// --- the desktop search box keeps room for its placeholder
{ const search = html.split('<script type="module" id="stocklens-search">')[1].split("</script>")[0];
  const ph = (search.match(/placeholder="([^"]+)"/) || [])[1]; ok(ph, "the search placeholder is found in the search module");
  const basis = +(css.match(/#globalBar \.gb-search\{flex:1 0 ([\d.]+)rem;/) || [])[1]; ok(basis >= 15, "desktop search basis " + basis + "rem and it never shrinks (flex-shrink 0)");
  const fontPx = 16 * +(css.match(/#globalBar #stockSearch input\{padding:8px 10px;font-size:([\d.]+)rem/) || [])[1];
  const room = basis * 16 - 2 * 10 - 2, need = ph.length * 0.56 * fontPx;   // 0.56em: a generous average glyph width for this face (measured 203px in a browser)
  ok(need <= room, "the placeholder '" + ph + "' (about " + Math.round(need) + "px) fits in " + room + "px"); }

// --- bar breakpoints: full row, tablet row, phone rows (container queries only)
{ const at = (q) => css.indexOf("@container (max-width:" + q + "px)");
  ok(at(1149) > 0 && at(599) > at(1149), "the bar has its tablet (<1150px) and phone (<600px) steps, in order");
  ok(!/@container \(max-width:1000px\)/.test(css), "the old 1000px step (links wrapped inside the bar at 1001-1149px) is gone");
  ok(/\.w\{max-width:1200px;margin:0 auto;padding:0 18px;container:page\/inline-size\}/.test(css) && /#globalBar \.gb-in\{[^}]*max-width:1200px/.test(css), "page and bar share the 1200px width");
  eq((html.match(/@media[^{]*\{/g) || []).filter((x) => !/prefers-color-scheme|max-width:640px/.test(x)).length, 0, "no new @media rule: every responsive rule is a container query"); }

// --- the agreed browser-check widths
{ const py = fs.readFileSync(__dirname + "/browser_checks.py", "utf8"); const w = (py.match(/^WIDTHS = \[([^\]]+)\]/m) || [])[1];
  eq(w.split(",").map((x) => +x.trim()), [320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440, 1920], "browser checks cover the 13 release-review widths and the earlier 1366 and 1920"); }

console.log("Responsive layout tests passed (" + checks + " checks)");
