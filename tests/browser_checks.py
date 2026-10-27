"""
Local browser checks of index.html against the SYNTHETIC preview site (tests/synthetic_site.py). Not run by CI.

    python tests/browser_checks.py <site-dir> <report-dir> [--no-shots]

Serves <site-dir> on 127.0.0.1 only, opens it in headless Chromium (Playwright) at each agreed width and records, per page:
horizontal overflow, the top bar's rows and any overlap between its parts, whether the search placeholder fits, touch targets
under 44px / 24px, text below WCAG AA contrast, and browser-console errors. Writes <report-dir>/report.json and screenshots.
Makes no request outside 127.0.0.1 (anything else is blocked and counted), so fonts fall back to the system font.
"""
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTHS = [320, 360, 375, 390, 430, 640, 768, 900, 1000, 1005, 1024, 1280, 1366, 1440, 1920]   # the 13 widths of the release review plus 1366 and 1920 from the first review
PAGES = [("home", ""), ("screener", "#screener"), ("tcs", "#stock=TCS")]

PROBE = r"""
() => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const de = document.documentElement, out = {};
  out.scrollWidth = de.scrollWidth; out.clientWidth = de.clientWidth; out.overflowX = de.scrollWidth > de.clientWidth + 1;
  out.wide = [...document.querySelectorAll("body *")].filter((e) => vis(e) && e.getBoundingClientRect().right > de.clientWidth + 1 && !e.closest(".scroll,.mk-scroll,[data-scroll]"))
    .slice(0, 8).map((e) => (e.id ? "#" + e.id : e.tagName.toLowerCase() + "." + (e.className && e.className.baseVal === undefined ? String(e.className).split(" ")[0] : "")) + " right=" + Math.round(e.getBoundingClientRect().right));
  /* content that spills out of its card or box (a scrolling region inside the box is allowed to hold wider content) */
  const nm = (e) => (e.id ? "#" + e.id : e.tagName.toLowerCase() + (typeof e.className === "string" && e.className ? "." + e.className.split(" ")[0] : ""));
  const scrolls = (e) => { const o = getComputedStyle(e).overflowX; return o === "auto" || o === "scroll" || o === "hidden" || o === "clip"; };
  out.spill = [];
  for (const box of document.querySelectorAll(".mk-card,.mk-idx,.mk-adv,.mk-tile,.tile,.fs,.c,.item,#screener-sec tbody tr,#detail > section,#detail > div,#detail > p")) {
    if (!vis(box)) continue; const B = box.getBoundingClientRect();
    for (const e of box.querySelectorAll("*")) { if (!vis(e)) continue; let inScroll = false;
      for (let x = e.parentElement; x && x !== box; x = x.parentElement) if (scrolls(x)) { inScroll = true; break; }
      if (inScroll) continue; const r = e.getBoundingClientRect();
      if (r.right > B.right + 1 || r.left < B.left - 1) { out.spill.push(nm(box) + " > " + nm(e) + " (" + Math.round(r.left) + "-" + Math.round(r.right) + " vs " + Math.round(B.left) + "-" + Math.round(B.right) + ")"); break; } } }
  out.spill = out.spill.slice(0, 10);
  /* text cut off inside a control, and controls hidden off-screen in a sideways-scrolling strip */
  out.clipped = [...document.querySelectorAll("button,a[href],label,[role=tab]")].filter((e) => vis(e) && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible").map(nm).slice(0, 10);
  out.offStrip = [];
  for (const strip of document.querySelectorAll("[role=tablist],.tabs,.chips")) { if (!vis(strip) || !scrolls(strip)) continue; const S = strip.getBoundingClientRect();
    for (const b of strip.querySelectorAll("button,[role=tab],a")) { if (!vis(b)) continue; const r = b.getBoundingClientRect(); if (r.right > S.right + 1 || r.left < S.left - 1) out.offStrip.push(nm(strip) + " > " + (b.textContent || "").trim().slice(0, 24)); } }
  /* a short number (e.g. -0.71% or 12,65,567.21) that wraps onto a second line */
  const NUMRE = /^[+\-\u2212]?\u20b9?[\d,]+(\.\d+)?\s?(%|x|pts)?$/;
  out.brokenNumbers = [...document.querySelectorAll("span,b,td,div,small")].filter((e) => vis(e) && e.children.length === 0 && NUMRE.test((e.textContent || "").trim()))
    .filter((e) => { const rg = document.createRange(); rg.selectNodeContents(e); const tops = new Set([...rg.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top))); return tops.size > 1; })   /* the text's own line boxes */
    .map((e) => nm(e) + ":" + e.textContent.trim()).slice(0, 10);
  const bar = document.querySelector("#globalBar .gb-in");
  if (bar) {
    const parts = [...bar.children].filter(vis), rowsOf = [];
    for (const e of parts) { const r = e.getBoundingClientRect(), mid = (r.top + r.bottom) / 2, row = rowsOf.find((x) => mid > x.top && mid < x.bottom);
      if (row) { row.top = Math.min(row.top, r.top); row.bottom = Math.max(row.bottom, r.bottom); } else rowsOf.push({ top: r.top, bottom: r.bottom }); }
    out.bar = { height: Math.round(bar.getBoundingClientRect().height), rows: rowsOf.length, parts: parts.map((e) => (e.id || e.className) + "@" + Math.round(e.getBoundingClientRect().top) + "+" + Math.round(e.getBoundingClientRect().height)) };
    const rects = parts.map((e) => [e.id || e.className, e.getBoundingClientRect()]), ov = [];
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) { const a = rects[i][1], b = rects[j][1];
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) ov.push(rects[i][0] + " x " + rects[j][0]); }
    const links = [...bar.querySelectorAll(".gb-sections a")].filter(vis).map((a) => a.getBoundingClientRect());
    for (let i = 0; i < links.length; i++) for (let j = i + 1; j < links.length; j++) { const a = links[i], b = links[j];
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) ov.push("nav link " + i + " x " + j); }
    out.bar.overlaps = ov;
  }
  const inp = document.getElementById("companySearch");
  if (inp && vis(inp)) {
    const s = getComputedStyle(inp), c = document.createElement("canvas").getContext("2d"); c.font = s.font;
    const need = c.measureText(inp.placeholder || "").width, have = inp.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    out.search = { width: Math.round(inp.getBoundingClientRect().width), height: Math.round(inp.getBoundingClientRect().height), placeholder: inp.placeholder,
                   placeholderFits: need <= have + 0.5, need: Math.round(need), have: Math.round(have) };
  } else out.search = null;
  const ctl = [...document.querySelectorAll("a[href],button,input,select,summary,[role=tab]")].filter(vis).filter((e) => !e.closest("table thead") || true);
  const small = (e, n) => { const l = e.tagName === "INPUT" && e.closest("label"), r = (l || e).getBoundingClientRect(); return r.height < n || r.width < n; };   /* an input inside its label: the label is the target */
  const label = (e) => (e.id ? "#" + e.id : e.tagName.toLowerCase()) + ":" + (e.textContent || e.value || e.getAttribute("aria-label") || "").trim().slice(0, 24);
  const inline = (e) => e.tagName === "A" && getComputedStyle(e).display === "inline" && e.closest("p,li,td,small,span");   /* links inside running text are exempt under WCAG 2.5.8 */
  out.controls = ctl.length;
  out.under44 = ctl.filter((e) => !inline(e) && small(e, 44)).map(label);
  out.under24 = ctl.filter((e) => !inline(e) && small(e, 24)).map(label);
  const lum = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map((v) => { v = v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const bgOf = (e) => { for (let x = e; x; x = x.parentElement) { const b = getComputedStyle(x).backgroundColor; const m = b.match(/[\d.]+/g); if (m && (m.length < 4 || +m[3] > 0.5)) return b; } return getComputedStyle(document.body).backgroundColor; };
  const low = [];
  for (const e of document.querySelectorAll("body *")) {
    if (!vis(e) || ![...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const s = getComputedStyle(e), L1 = lum(s.color), L2 = lum(bgOf(e)); if (L1 === null || L2 === null) continue;
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05), big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    if (ratio < (big ? 3 : 4.5)) low.push(e.textContent.trim().slice(0, 30) + " (" + ratio.toFixed(2) + ")");
  }
  out.lowContrast = [...new Set(low)].slice(0, 15); out.lowContrastCount = low.length;
  return out;
}
"""


def serve(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    h = functools.partial(Quiet, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(site, report_dir, shots=True, widths=WIDTHS, pages=PAGES, schemes=("light",)):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    srv = serve(site)
    base = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
    results, blocked = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in schemes:
            for w in widths:
                ctx = browser.new_context(viewport={"width": w, "height": 900}, color_scheme=scheme)
                ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else (blocked.append(r.request.url), r.abort()))
                for name, frag in pages:
                    pg = ctx.new_page()
                    errs = []
                    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                    pg.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
                    pg.goto(base + frag)
                    pg.wait_for_timeout(900)
                    if frag.startswith("#") and not frag.startswith("#stock="):
                        pg.evaluate("(id)=>{const e=document.getElementById(id);if(e)e.scrollIntoView()}", frag[1:])
                    r = pg.evaluate(PROBE)
                    r.update({"page": name, "width": w, "scheme": scheme,
                              "consoleErrors": [e for e in errs if "net::ERR_FAILED" not in e and "Failed to load resource" not in e]})
                    if shots:
                        f = report_dir / ("%s-%s-%d.png" % (name, scheme, w))
                        pg.screenshot(path=str(f), full_page=True)
                        r["screenshot"] = str(f)
                    results.append(r)
                    pg.close()
                ctx.close()
        browser.close()
    srv.shutdown()
    (report_dir / "report.json").write_text(json.dumps({"results": results, "blockedRequests": sorted(set(blocked))}, indent=1), encoding="utf-8")
    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    res = run(sys.argv[1], sys.argv[2], shots="--no-shots" not in sys.argv, schemes=("light", "dark"))
    for r in res:
        b = r.get("bar") or {}
        s = r.get("search") or {}
        print("%-5s %-8s %5d overflow=%-5s bar_rows=%s overlaps=%d ph_fits=%s spill=%d clipped=%d offstrip=%d brokenNum=%d under44=%d under24=%d lowC=%d errors=%d" % (
            r["scheme"], r["page"], r["width"], r["overflowX"], b.get("rows"), len(b.get("overlaps", [])), s.get("placeholderFits"), len(r["spill"]), len(r["clipped"]),
            len(r["offStrip"]), len(r["brokenNumbers"]), len(r["under44"]), len(r["under24"]), r["lowContrastCount"], len(r["consoleErrors"])))
