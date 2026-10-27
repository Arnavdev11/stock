"""
Real-browser CONTENT checks of index.html on a local preview (not run by CI): what the page says, not only how it is laid out.

    python tests/browser_content_checks.py <site-dir> [symbol ...]

For the home page, the screen, the comparison page and each stock page (default: every stock in the site's out/historical.json), at 390px and
1280px, records: page errors; leaked placeholders in visible text (undefined, NaN, null, [object Object], Infinity); internal links (#...) whose
target does not exist; links with an empty, "#", "undefined" or javascript: address (except the brand and the dashboard menu items, which are scripted controls); and the empty-state messages shown, per section, so a
reviewer can tell an honest "not on file" from a missing value. Writes nothing; serves the site on 127.0.0.1 only. Exit 0 only if no page error,
leaked placeholder or broken link was found.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_checks import serve  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PROBE = r"""
() => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const root = document.body, walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), leaks = [];
  const BAD = /(^|[^A-Za-z])(undefined|NaN|null|\[object Object\]|Infinity)([^A-Za-z]|$)/;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) { const p = n.parentElement; if (!p || p.closest("script,style") || !vis(p)) continue; const t = n.textContent.trim(); if (t && BAD.test(t)) leaks.push(t.slice(0, 60)); }
  const links = [...document.querySelectorAll("a")].filter(vis), badLinks = [], hashMiss = [], external = [];
  /* known controls written as href="#": the brand (back to the dashboard) and the dashboard menu items that ask for a stock through the search (stocklens-nav) */
  const control = (a) => a.id === "gbBrand" || a.hasAttribute("data-gb-pick");
  for (const a of links) { const h = (a.getAttribute("href") || "").trim();
    if ((!h || h === "#") && control(a)) continue;
    if (!h || h === "#" || /^javascript:|undefined|null/i.test(h)) { badLinks.push((a.textContent || "").trim().slice(0, 30) + " -> '" + h + "'"); continue; }
    if (/^https?:/i.test(h)) { external.push(h); continue; }
    const m = /^#([A-Za-z][\w-]*)$/.exec(h); if (m && !/^(stock|home|compare|section|screener)/.test(m[1]) && !document.getElementById(m[1])) hashMiss.push(h); }
  const EMPTY = /(not loaded yet|Unavailable|unavailable|No quarterly results|not on file|Data unavailable|not available|could not be loaded|No stocks match|Not enough values)/;
  const empties = {};
  for (const sec of document.querySelectorAll("section[id], [id^=detail], #compareView, .mk-sec")) { if (!vis(sec)) continue; const id = sec.id || "?";
    const hits = new Set(); const w2 = document.createTreeWalker(sec, NodeFilter.SHOW_TEXT);
    for (let n = w2.nextNode(); n; n = w2.nextNode()) { const t = n.textContent.trim(); const mm = EMPTY.exec(t); if (mm && n.parentElement && vis(n.parentElement)) hits.add(t.slice(0, 70)); }
    if (hits.size) empties[id] = [...hits].slice(0, 6); }
  return { leaks: [...new Set(leaks)].slice(0, 10), badLinks, hashMiss: [...new Set(hashMiss)], external: [...new Set(external)], empties, links: links.length };
}
"""


def main(site, symbols):
    site = Path(site)
    if not symbols:
        h = json.loads((site / "out" / "historical.json").read_text())
        symbols = sorted((h.get("stocks") or {}).keys())
    pages = [("home", ""), ("screener", "#screener"), ("compare", "#compare=" + ",".join(symbols[:3]))] + [("stock " + s, "#stock=" + s) for s in symbols]
    srv = serve(site)
    base = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
    report, problems = [], 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w in (390, 1280):
            ctx = b.new_context(viewport={"width": w, "height": 900})
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
            for name, frag in pages:
                pg = ctx.new_page()
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(base + frag)
                pg.wait_for_timeout(2500)
                r = pg.evaluate(PROBE)
                r.update({"page": name, "width": w, "errors": errs})
                bad = len(errs) + len(r["leaks"]) + len(r["badLinks"]) + len(r["hashMiss"])
                problems += bad
                report.append(r)
                print("%-18s %4d  errors=%d leaks=%d badLinks=%d missingTargets=%d links=%d external=%d" % (name, w, len(errs), len(r["leaks"]), len(r["badLinks"]), len(r["hashMiss"]), r["links"], len(r["external"])))
                pg.close()
            ctx.close()
        b.close()
    srv.shutdown()
    out = site.parent / (site.name + "-content-report.json")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\n%d page views, %d problems; report: %s" % (len(report), problems, out))
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2:]))
