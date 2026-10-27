"""
Real-browser behaviour checks of the Fundamental screen (filters, Load more, tabs, row links, mobile cards) on the SYNTHETIC preview site. Not run by CI.

    python tests/browser_screener_checks.py <scratch-dir>

Builds two synthetic sites in <scratch-dir> (tests/synthetic_site.py: one as generated, one with every sector removed), serves each on 127.0.0.1
only, and asserts in headless Chromium. Exit code 0 only if every check passed. No network request leaves 127.0.0.1.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_site          # noqa: E402
from browser_checks import serve  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    print(("ok   " if cond else "FAIL ") + msg)


JS = {
    "visible": "(id)=>[...document.querySelectorAll('#'+id+' tbody tr')].filter(r=>!r.hidden&&r.getBoundingClientRect().height>0&&!r.querySelector('td[colspan]')).map(r=>r.cells[0].textContent.trim())",
    "all": "(id)=>[...document.querySelectorAll('#'+id+' tbody tr')].filter(r=>!r.querySelector('td[colspan]')).map(r=>r.cells[0].textContent.trim())",
    "pager": "(id)=>{const t=document.querySelector('#'+id+' table')||document.getElementById(id);const b=t&&t.nextElementSibling;if(!b||!b.classList.contains('fs-more'))return null;const btn=b.querySelector('button');const r=btn.getBoundingClientRect();return{say:b.querySelector('p').textContent,btn:btn.hidden?null:btn.textContent,h:Math.round(r.height)}}",
}


def site_checks(page, base, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base)
    page.wait_for_timeout(900)
    v = lambda tid: page.evaluate(JS["visible"], tid)
    a = lambda tid: page.evaluate(JS["all"], tid)
    pg = lambda tid: page.evaluate(JS["pager"], tid)
    total = len(json.loads(Path(SITE, "out", "fundamentals.json").read_text())["stocks"])
    page.select_option("#roe", "0")
    page.wait_for_timeout(100)
    check(len(a("ftable")) == total, "%dpx: no filter lists all %d stocks (coverage kept)" % (width, total))
    check(len(v("ftable")) == 50, "%dpx: the first 50 are shown" % width)
    p = pg("ftable")
    check(p and p["say"] == "Showing 50 of %d stocks" % total and p["btn"] == "Show 50 more", "%dpx: the line says 50 of %d and offers 50 more (%s)" % (width, total, p))
    check(p and p["h"] >= 44, "%dpx: the Show more button is a 44px target" % width)
    nxt = a("ftable")[50]
    page.click("#ftable + .fs-more button")
    page.wait_for_timeout(100)
    check(len(v("ftable")) == 100, "%dpx: Show more shows 100" % width)
    focused = page.evaluate("()=>{const e=document.activeElement;return e&&e.tagName==='TD'?e.textContent.trim():null}")
    check(focused == nxt, "%dpx: focus moves to the first newly shown stock (%s)" % (width, focused))
    page.click('#ftable [data-k="symbol"]')
    page.wait_for_timeout(100)
    check(len(v("ftable")) == 100 and v("ftable")[0] == sorted(a("ftable"))[0], "%dpx: sorting keeps the number shown and sorts all rows, not just the visible ones" % width)
    page.click("#ftable + .fs-more button")
    page.wait_for_timeout(100)
    p = pg("ftable")
    check(len(v("ftable")) == total and p["btn"] is None and p["say"] == "Showing all %d stocks" % total, "%dpx: the last page shows all and hides the button" % width)
    page.fill("#fsPeMax", "30")
    page.wait_for_timeout(100)
    shown = v("ftable")
    check(len(shown) == min(50, len(a("ftable"))), "%dpx: a filter change starts again at 50" % width)
    pe = page.evaluate("()=>[...document.querySelectorAll('#ftable tbody tr')].map(r=>r.cells[3].textContent)")
    check(all(x != "-" and 0 < float(x.replace(",", "")) <= 30 for x in pe), "%dpx: P/E max 30 lists only positive P/E up to 30" % width)
    # the other tabs follow the same filters
    page.select_option("#fsSector", "Financials")
    page.wait_for_timeout(150)
    t1 = sorted(a("ftable"))
    page.click("#ftabs button:nth-child(2)")
    page.wait_for_timeout(150)
    check(sorted(a("t2a")) == t1 and sorted(a("t2b")) == t1 and sorted(a("t2")) == t1, "%dpx: Financial Health lists exactly the stocks the filters pass (%d)" % (width, len(t1)))
    page.click("#ftabs button:nth-child(3)")
    page.wait_for_timeout(150)
    check(sorted(a("t3a")) == t1 and sorted(a("t3b")) == t1, "%dpx: Cash Flow & Growth too" % width)
    page.click("#fsReset")
    page.wait_for_timeout(150)
    check(len(v("t3a")) == 50 and pg("t3a")["btn"] == "Show 50 more", "%dpx: the other tabs page at 50 as well" % width)
    page.click("#ftabs button:nth-child(1)")
    page.select_option("#fsPeMode", "negative")
    page.wait_for_timeout(100)
    pe = page.evaluate("()=>[...document.querySelectorAll('#ftable tbody tr')].map(r=>r.cells[3].textContent)")
    check(pe and all(x.startswith("-") and x != "-" for x in pe) and page.is_disabled("#fsPeMax"), "%dpx: Negative only (loss-making) lists only negative P/E: %s" % (width, pe))
    page.select_option("#fsPeMode", "any")
    page.select_option("#fsPbMode", "negative")
    page.wait_for_timeout(100)
    check(v("ftable") == ["SIGMA"], "%dpx: P/B Negative only lists the negative-book-value stock" % width)
    page.select_option("#fsPbMode", "any")
    page.fill("#fsPbMax", "3")
    page.wait_for_timeout(100)
    check("SIGMA" not in a("ftable"), "%dpx: a P/B maximum does not list negative P/B" % width)
    page.fill("#fsPbMax", "abc")
    page.wait_for_timeout(100)
    check(page.get_attribute("#fsPbMax", "aria-invalid") == "true" and page.is_visible("#fsErr") and a("ftable") == [], "%dpx: invalid input is marked, explained and shows no rows" % width)
    page.click("#fsReset")
    page.wait_for_timeout(100)
    first = v("ftable")[0]
    page.click("#ftable tbody tr:first-child td:first-child")
    page.wait_for_timeout(300)
    check(page.evaluate("()=>location.hash") == "#stock=" + first, "%dpx: a stock link still opens its page (%s)" % (width, first))


SITE = None


def main(scratch):
    global SITE
    scratch = Path(scratch)
    SITE = scratch / "screener-site"
    nosec = scratch / "screener-site-nosector"
    for d in (SITE, nosec):
        if d.exists():
            raise SystemExit("use a new scratch directory: %s exists" % d)
    synthetic_site.build(SITE)
    synthetic_site.build(nosec)
    f = nosec / "out" / "fundamentals.json"
    doc = json.loads(f.read_text())
    for s in doc["stocks"]:
        s["sector"] = None
    f.write_text(json.dumps(doc))
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for root, fn in ((SITE, "full"), (nosec, "nosector")):
            srv = serve(root)
            base = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
            ctx = b.new_context()
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            if fn == "full":
                for w in (1280, 390):
                    site_checks(page, base, w)
            else:
                page.goto(base)
                page.wait_for_timeout(900)
                check(page.is_disabled("#fsSector"), "no sector data: the Sector filter is off")
                note = page.text_content("#fsSectorNote")
                check("No sector is published" in note, "and says why: " + note)
            ctx.close()
            srv.shutdown()
        b.close()
    check(not errors, "no page errors (%s)" % errors[:2])
    bad = [m for c, m in CHECKS if not c]
    print("\n%d checks, %d passed, %d failed" % (len(CHECKS), len(CHECKS) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
