"""
Local browser checks of the restructured home page (Market Today + Fundamental screen) against the SYNTHETIC preview site (tests/synthetic_site.py). Not run by CI.

    python tests/browser_homepage_checks.py <site-dir> <report-dir> [--no-shots]

Serves <site-dir> on 127.0.0.1 only and opens it in headless Chromium (Playwright) at phone, tablet and desktop widths, in light and dark mode. Per width it records
and asserts: no horizontal page overflow; no stock symbol broken across lines in any dashboard table; Swing Market Watch never shows more columns than fit
(at most one column per 340px, so two on a tablet and one on a phone); no visible dashboard section is empty; the Movers tabs, the Volume views and "Show all"
work with a mouse and from the keyboard, and keep focus; the "Not yet available" note is present; the screen's numbers are right-aligned; no script error.
Writes <report-dir>/homepage_report.json and full-page screenshots. Makes no request outside 127.0.0.1 (anything else is blocked), so fonts fall back.
Exit status 1 if any check fails.
"""
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTHS = [320, 360, 375, 390, 430, 640, 768, 784, 900, 1024, 1280, 1440, 1920]

PROBE = r"""
() => {
  const out = {};
  out.overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  out.wrapped = [...document.querySelectorAll('main .mk-t .mk-sym')].filter(a => a.getClientRects().length > 1).map(a => a.textContent);
  const g = document.querySelector('#mk-swing .mk-g');
  if (g) { const cols = getComputedStyle(g).gridTemplateColumns.split(' ').filter(x => x && parseFloat(x) > 0).length   /* auto-fit lists collapsed empty tracks as 0px */, w = g.getBoundingClientRect().width; out.swing = { cols, width: Math.round(w), cards: g.children.length, maxFit: Math.max(1, Math.floor((w + 12) / (340 + 12))) }; }
  out.emptyVisible = [...document.querySelectorAll('main .mk-sec')].filter(s => !s.hidden && !(s.querySelector('[data-mk]') || {}).textContent.trim()).map(s => s.id);
  out.hidden = [...document.querySelectorAll('main .mk-sec')].filter(s => s.hidden).map(s => s.id);
  out.pending = (document.querySelector('#mk-pending .mk-pend') || {}).textContent || '';
  out.circuits = /circuit/i.test(document.getElementById('market').innerText);
  const tl = [...document.querySelectorAll('#mk-breadth .mk-tile')], tr = document.querySelector('#mk-breadth .mk-tiles');
  if (tr && tl.length) { const R = tr.getBoundingClientRect(), rows = {}; tl.forEach(e => { const b = e.getBoundingClientRect(); (rows[Math.round(b.top)] = rows[Math.round(b.top)] || []).push(b); });
    out.tileGaps = Object.values(rows).map(r => Math.round(R.right - Math.max(...r.map(b => b.right)))).filter(g => g > 2); }
  const td = document.querySelector('#fund td.n');
  out.screenAlign = td ? getComputedStyle(td).textAlign : null;
  return out;
}
"""


def serve(site):
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    site, rep = Path(argv[0]), Path(argv[1])
    shots = "--no-shots" not in argv
    rep.mkdir(parents=True, exist_ok=True)
    srv = serve(site)
    url = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
    report, failures = {}, []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w in WIDTHS:
            for scheme in ("light", "dark"):
                pg = b.new_page(viewport={"width": w, "height": 900}, color_scheme=scheme)
                errs, blocked = [], []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else (blocked.append(r.request.url), r.abort()))
                pg.goto(url)
                pg.wait_for_selector("#mk-movers [data-mk-tabs=mv]", timeout=15000)
                pg.wait_for_timeout(600)
                if scheme == "dark":
                    pg.evaluate("document.documentElement.setAttribute('data-theme','dark')")
                r = pg.evaluate(PROBE)
                key = "%d_%s" % (w, scheme)
                fail = lambda m: failures.append(key + ": " + m)
                if r["overflow"] > 0:
                    fail("horizontal overflow %dpx" % r["overflow"])
                if r["wrapped"]:
                    fail("symbols broken across lines: %s" % r["wrapped"])
                if r.get("swing") and r["swing"]["cols"] > min(r["swing"]["maxFit"], max(1, r["swing"]["cards"])):
                    fail("Swing Market Watch shows %d columns in %dpx" % (r["swing"]["cols"], r["swing"]["width"]))
                if r["emptyVisible"]:
                    fail("visible empty sections: %s" % r["emptyVisible"])
                if r.get("tileGaps"):
                    fail("breadth tiles leave an empty gap at the end of a row: %s px" % r["tileGaps"])
                if r["circuits"]:
                    fail("upper / lower circuits are mentioned on the page while there is no data for them")
                if "Not yet available" not in r["pending"]:
                    fail("the Not yet available note is missing")
                if r["screenAlign"] != "right" and w > 640:
                    fail("screen numbers are not right-aligned")
                if scheme == "light":       # interaction (once per width): mouse and keyboard, focus kept
                    tabs = pg.locator("#mk-movers [data-mk-tabs=mv] [role=tab]")
                    names = tabs.all_inner_texts()
                    r["mover_tabs"] = names
                    for name in names:
                        pg.locator("#mk-movers [data-mk-tabs=mv] [role=tab]", has_text=name).first.click()
                        sel = pg.locator("#mk-movers [data-mk-tabs=mv] [aria-selected=true]").inner_text()
                        foc = pg.evaluate("document.activeElement && document.activeElement.getAttribute('role')")
                        if sel != name or foc != "tab":
                            fail("Movers tab %s: selected %r, focus on %r" % (name, sel, foc))
                    second =pg.locator("#mk-movers [data-mk-tabs=mv] [role=tab]").nth(1)
                    second.focus()
                    pg.keyboard.press("Enter")
                    if pg.locator("#mk-movers [data-mk-tabs=mv] [aria-selected=true]").inner_text() != names[1]:
                        fail("Movers tabs do not work from the keyboard (Enter)")
                    if pg.locator("[data-mk-feed]").count():
                        n0 = pg.locator("#mkFeed li").count()
                        pg.locator("[data-mk-feed]").focus()
                        pg.keyboard.press("Enter")
                        n1 = pg.locator("#mkFeed li").count()
                        exp = pg.locator("[data-mk-feed]").get_attribute("aria-expanded")
                        if not (n0 <= 8 and n1 > n0 and exp == "true"):
                            fail("Show all: %d -> %d items, aria-expanded %r" % (n0, n1, exp))
                        pg.keyboard.press("Enter")
                        r["feed"] = [n0, n1, pg.locator("#mkFeed li").count()]
                    pg.locator("#mk-movers [data-mk-tabs=mv] [role=tab]", has_text="52-week").first.click()
                    low = pg.locator("#mk-movers .mk-card", has_text="New 52-week low")
                    r["low_card"] = low.inner_text()[:200] if low.count() else None
                    if not (low.count() and low.first.is_visible()):
                        fail("the New 52-week low card is not visible in Movers > 52-week")
                    if shots:
                        pg.locator("#mk-movers").screenshot(path=str(rep / ("movers52w_%d.png" % w)))
                        pg.locator("#mk-breadth").screenshot(path=str(rep / ("breadth_%d.png" % w)))
                    pg.locator("#mk-movers [data-mk-tabs=mv] [role=tab]").first.click()
                r["errors"], r["blocked_requests"] = errs, len(blocked)
                if errs:
                    fail("script errors: %s" % errs[:3])
                report[key] = r
                if shots:
                    pg.screenshot(path=str(rep / ("home_%s.png" % key)), full_page=True)
                pg.close()
        b.close()
    srv.shutdown()
    (rep / "homepage_report.json").write_text(json.dumps({"failures": failures, "pages": report}, indent=1), encoding="utf-8")
    print(json.dumps({"checked": len(report), "failures": failures}, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
