"use strict";
// Run: node tests/test_detail_groups.js   (no network, no browser)
// Two-column stock page, stages 1 and 2 only (RESPONSIVE_REDESIGN.md). Stage 1: the Investor Snapshot's overviewHeading() and the section menu's heading()
// also accept a heading inside a direct-child .sd-group of #detail, and behave exactly as before on the flat page. Stage 2: view(s,d) wraps three
// existing runs of lines in .sd-group openers and closers written as separate lines, every heading line byte-identical, no layout CSS, and none of
// the inserted lines contains the word the guard tests probe. The rendered pages are checked in a browser with tests/browser_content_checks.py.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/../index.html", "utf8");
const { withoutDecisionEdits } = require("./legacy_5i.js");
let checks = 0;
const ok = (c, m) => { checks++; assert.ok(c, m); }, eq = (a, b, m) => { checks++; assert.deepStrictEqual(a, b, m); };

const fnSource = (code, name) => { const i = code.indexOf("function " + name + "("); assert.ok(i >= 0, name + " found"); const j = code.indexOf("\n  return null}", i); return code.slice(i, j + "\n  return null}".length); };
const block = (id) => html.slice(html.indexOf('<script type="module" id="' + id + '">'), html.indexOf("</script>", html.indexOf('<script type="module" id="' + id + '">')));
const overviewHeading = new Function(fnSource(block("stocklens-snapshot"), "overviewHeading") + "\nreturn overviewHeading;")();
const heading = new Function(fnSource(block("stocklens-nav"), "heading") + "\nreturn heading;")();

// a minimal DOM: just what the two functions read
function el(tag, cls, kids, text) {
  const e = { tagName: tag.toUpperCase(), className: cls || "", children: kids || [], parentNode: null, _text: text || "" };
  e.children.forEach((k) => { k.parentNode = e; });
  e.firstElementChild = e.children[0] || null;
  Object.defineProperty(e, "textContent", { get: () => e._text + e.children.map((k) => k.textContent).join("") });
  e.querySelectorAll = (sel) => { const out = [], walk = (n) => n.children.forEach((k) => { if (k.tagName === sel.toUpperCase()) out.push(k); walk(k); }); walk(e); return out; };
  return e;
}
const h3 = (t) => el("h3", "", [], t), p = (t) => el("p", "note", [], t), tiles = () => el("div", "tiles", [el("div", "tile", [], "P/E 20")]);

// --- stage 1 on today's flat page: unchanged behaviour
{ const ov = h3("Overview"), fh = h3("Financial Health (₹ crore)"), cf = h3("Cash Flow (₹ crore)");
  const box = el("div", "", [el("h2", "", [], "Stock Detail: TCS"), ov, tiles(), p("Fundamentals data"), fh, tiles(), cf, tiles()]);
  eq(overviewHeading(box), ov, "flat page: the snapshot anchor is the Overview heading itself (as before)");
  eq(heading(box, "Overview"), ov, "flat page: the Fundamentals menu item finds the Overview heading");
  eq(heading(box, "Financial Health"), fh, "flat page: Financials finds Financial Health"); eq(heading(box, "Cash Flow"), cf, "flat page: Cash Flow found"); }

// --- stage 1 on a grouped page
{ const ov = h3("Overview"), fh = h3("Financial Health (₹ crore)"), cf = h3("Cash Flow (₹ crore)");
  const g1 = el("div", "sd-group", [ov, tiles(), p("Fundamentals data")]), g2 = el("div", "sd-group", [fh, p("Income statement"), tiles()]), g3 = el("div", "sd-group", [cf, tiles()]);
  const box = el("div", "", [el("h2", "", [], "Stock Detail: TCS"), el("div", "tiles", []), g1, g2, g3, el("div", "", [], "chart")]);
  eq(overviewHeading(box), g1, "grouped page: the snapshot anchor is the group that opens with the Overview heading, a direct child of #detail (so insertBefore keeps working)");
  eq(heading(box, "Overview"), ov, "grouped page: the menu still scrolls to the heading itself");
  eq(heading(box, "Financial Health"), fh, "grouped page: Financial Health found inside its group"); eq(heading(box, "Cash Flow"), cf, "grouped page: Cash Flow found inside its group"); }

// --- stage 1: what must still NOT match
{ const deep = el("div", "sd-group", [el("div", "", [h3("Overview")])]);
  eq(overviewHeading(el("div", "", [deep])), null, "a heading nested deeper than the group's own children is not an anchor");
  eq(heading(el("div", "", [deep]), "Overview"), null, "nor a menu destination");
  const later = el("div", "sd-group", [p("x"), h3("Overview")]);
  eq(overviewHeading(el("div", "", [later])), null, "a group that does not open with the heading is not the snapshot anchor");
  const other = el("div", "panel", [h3("Overview")]);
  eq(overviewHeading(el("div", "", [other])), null, "a heading inside some other wrapper (a module's own panel) is not the anchor");
  eq(heading(el("div", "", [other]), "Overview"), null, "nor a menu destination (modules' own headings stay out of the menu)");
  eq(overviewHeading(el("div", "", [el("div", "sd-groups", [h3("Overview")])])), null, "the class must be exactly sd-group");
  { const g = el("div", "x sd-group", [h3("Overview")]); eq(overviewHeading(el("div", "", [g])), g, "sd-group may sit among other classes"); } }

// --- stage 2: the grouping lines in view(s,d)
const detail = html.slice(html.indexOf("function view(s,d){"), html.indexOf("\n  return h}", html.indexOf("function view(s,d){")));
const base = withoutDecisionEdits(html), baseDetail = base.slice(base.indexOf("function view(s,d){"), base.indexOf("\n  return h}", base.indexOf("function view(s,d){")));
const lines = detail.split("\n"), groupLines = lines.filter((l) => /sd-group|<\/div>';\s+\/\* group /.test(l));
const opens = lines.filter((l) => /^  h\+='<div class="sd-group" id="sd[A-Z][a-z]+">';\s+\/\* group [a-z]+ open \*\/$/.test(l)), closes = lines.filter((l) => /^  h\+='<\/div>';\s+\/\* group [a-z]+ close \*\/$/.test(l));
eq(opens.length, 3, "three group openers, each a line of its own"); eq(closes.length, 3, "three matching closers, each a line of its own"); eq(groupLines.length, 6, "and no other line mentions a group");
eq(groupLines.filter((l) => /overview/i.test(l)), [], "no inserted grouping line, id or comment contains the word the guard tests probe");
eq(lines.filter((l) => !baseDetail.split("\n").includes(l)), groupLines, "the only lines of view(s,d) that differ from the page before the recorded edits are the grouping lines");
eq(lines.filter((l) => !groupLines.includes(l)), baseDetail.split("\n"), "every other line keeps its order; that those lines (every heading line included) are byte-identical to the shipped page is enforced by the Stock Detail block pins in test_checklist.js, test_research.js and test_profiles.js");
{ let depth = 0, max = 0; for (const l of lines) { if (opens.includes(l)) depth++; if (closes.includes(l)) depth--; ok(depth >= 0, "groups never close before they open"); max = Math.max(max, depth); } eq(depth, 0, "every group is closed"); eq(max, 1, "groups are not nested"); }
const ids = opens.map((l) => /id="([^"]+)"/.exec(l)[1]); eq(new Set(ids).size, 3, "group ids are distinct");
ids.forEach((id) => eq((html.match(new RegExp('id="' + id + '"', "g")) || []).length, 1, id + " is used once in the page"));
const inside = (open) => { const i = lines.indexOf(open); const j = lines.findIndex((l, k) => k > i && closes.includes(l)); return lines.slice(i + 1, j); };
ok(/^  h\+='<h3[^>]*>Overview<\/h3>/.test(inside(opens[0])[0]), "the first group opens with the Overview heading line");
ok(/^  h\+='<h3[^>]*>Financial Health /.test(inside(opens[1])[0]), "the second opens with Financial Health");
ok(/^  h\+='<h3[^>]*>Cash Flow /.test(inside(opens[2])[0]), "the third opens with Cash Flow");
ok(!opens.concat(closes).some((l) => /detailChart|detailTech/.test(l)) && !opens.map(inside).flat().some((l) => /detailChart|detailTech/.test(l)), "the chart and the Technical Snapshot stay outside every group");
{ const css = html.slice(html.indexOf("<style>"), html.indexOf("</style></head>")); const all = html.split("<script").map((b) => b.split("</script>")[0]);
  ok(!/sd-group|sdRatios|sdHealth|sdCash/.test(css), "no layout CSS for the groups (stage 3 is not implemented)");
  eq(all.filter((b) => /\.sd-group\s*\{|sd-group[^"'\n]*\{[^}]*grid/.test(b)), [], "and no script adds any"); }

console.log("Detail group tests passed (" + checks + " checks)");
