"""
Scale checks of the Fundamental screen against an independent oracle, in real Chromium, on a LARGE SYNTHETIC list (not the NIFTY 500 data).
Not run by CI.

    python tests/browser_screener_scale_checks.py <new-scratch-dir> [stocks=520]

Builds tests/synthetic_site.py with --stocks N in the scratch dir (invented values, including negative, missing and text P/E and negative P/B),
recomputes in Python which stocks each filter should pass and in what order they should sort (the documented rules), and compares with the page:
all rows (including those hidden behind Load more), the visible rows after each "Show 50 more", duplicates, the count line, the pager line, the
filters and sort staying as they were after Load more, the other two tabs, keyboard operation, and 44px phone controls. Exit 0 only if all pass.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_site            # noqa: E402
from browser_checks import serve  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    print(("ok   " if cond else "FAIL ") + msg)


def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def passes(x, c):
    """The documented rules (RESPONSIVE_REDESIGN.md, Screener behaviour), written independently of the page's code."""
    for k in ("pe", "pb"):
        mode, lo, hi = c.get(k + "Mode", "any"), c.get(k + "Min"), c.get(k + "Max")
        v = x.get(k)
        if mode == "negative":
            if not (isnum(v) and v < 0):
                return False
        elif mode == "positive" or lo is not None or hi is not None:
            if not (isnum(v) and v > 0) or (lo is not None and v < lo) or (hi is not None and v > hi):
                return False
    if c.get("roe") and not (isnum(x.get("roe")) and x["roe"] >= c["roe"]):
        return False
    if c.get("sector") and x.get("sector") != c["sector"]:
        return False
    return True


def order(rows, key, asc):
    def k(item):
        i, x = item
        v = x.get(key)
        if key in ("symbol", "company_name", "sector"):
            ok = isinstance(v, str) and v != ""
            return (0 if ok else 1, (v if ok else ""), i) if asc else (0 if ok else 1, "", i)
        if not isnum(v):
            return (2, 0, i)
        tier = 0 if (key not in ("pe", "pb") or v > 0) else 1
        return (tier, v if asc else -v, i)
    s = sorted(enumerate(rows), key=k)
    if key in ("symbol", "company_name", "sector") and not asc:        # text, descending: reverse the valued ones, ties keep file order
        val = [it for it in s if isinstance(it[1].get(key), str) and it[1][key]]
        rest = [it for it in s if it not in val]
        val = sorted(val, key=lambda it: (it[1][key], -it[0]), reverse=True)
        s = val + rest
    return [x["symbol"] for _, x in s]


JS_ALL = "()=>[...document.querySelectorAll('#fund tr')].filter(r=>!r.querySelector('td[colspan]')).map(r=>r.cells[0].textContent.trim())"
JS_VIS = "()=>[...document.querySelectorAll('#fund tr')].filter(r=>!r.hidden&&!r.querySelector('td[colspan]')).map(r=>r.cells[0].textContent.trim())"
JS_TAB = "(id)=>[...document.querySelectorAll('#'+id+' tbody tr')].filter(r=>!r.querySelector('td[colspan]')).map(r=>r.cells[0].textContent.trim())"
JS_STATE = "()=>({peMax:fsPeMax.value,sector:fsSector.value,roe:roe.value,peMode:fsPeMode.value,sort:[...document.querySelectorAll('#ftable th')].map(t=>t.getAttribute('aria-sort')),count:fsCount.textContent,pager:(document.querySelector('#ftable + .fs-more p')||{}).textContent||null,more:(()=>{const b=document.querySelector('#ftable + .fs-more button');return b&&!b.hidden?b.textContent:null})()})"


def main(scratch, n):
    scratch = Path(scratch)
    site = scratch / "scale-site"
    if site.exists():
        raise SystemExit("use a new scratch directory")
    synthetic_site.build(site, n)
    data = json.loads((site / "out" / "fundamentals.json").read_text())["stocks"]
    total = len(data)
    srv = serve(site)
    base = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base)
        pg.wait_for_timeout(1200)
        ev = pg.evaluate

        def settle():
            pg.wait_for_timeout(120)

        # 1. default: ROE at least 15, sorted by ROE high-low
        want = [x for x in data if passes(x, {"roe": 15})]
        check(sorted(ev(JS_ALL)) == sorted(x["symbol"] for x in want), "default (ROE >= 15): exactly the %d stocks the rules pass, of %d" % (len(want), total))
        check(ev(JS_ALL) == order(want, "roe", False), "and in ROE order, highest first, ties in file order")
        st = ev(JS_STATE)
        check(st["count"] == "%d of %d stocks match the filters." % (len(want), total), "count line: " + st["count"])
        check(st["pager"] == "Showing 50 of %d stocks" % len(want) and len(ev(JS_VIS)) == 50, "first 50 shown: " + str(st["pager"]))

        # 2. filter + sort, then Load more to the end: filters and sort stay, rows stay in order, no duplicates
        pg.select_option("#roe", "20"); pg.fill("#fsPeMax", "60"); settle()
        pg.click('#ftable [data-k="pe"]'); settle(); pg.click('#ftable [data-k="pe"]'); settle()     # P/E low to high
        crit = {"peMax": 60.0, "roe": 20}
        want = [x for x in data if passes(x, crit)]
        exp = order(want, "pe", True)
        check(ev(JS_ALL) == exp, "P/E <= 60 and ROE >= 20, P/E low-high: %d rows, exactly the oracle's set and order" % len(exp))
        check(len(exp) > 100, "(enough rows to need several Show more clicks)")
        before = ev(JS_STATE)
        shown, rounds = ev(JS_VIS), 0
        while ev(JS_STATE)["more"] and rounds < 20:
            pg.click("#ftable + .fs-more button"); settle(); rounds += 1
            vis = ev(JS_VIS)
            check(vis == exp[:len(vis)] and len(vis) == min(50 * (rounds + 1), len(exp)), "Show more #%d: %d visible, a prefix of the sorted list" % (rounds, len(vis)))
            after = ev(JS_STATE)
            check({k: after[k] for k in ("peMax", "sector", "roe", "peMode", "sort", "count")} == {k: before[k] for k in ("peMax", "sector", "roe", "peMode", "sort", "count")},
                  "Show more #%d keeps the filters, the sort and the count" % rounds)
        vis = ev(JS_VIS)
        check(len(vis) == len(set(vis)) == len(exp), "after Load more: all %d shown, no duplicates" % len(exp))
        check(ev(JS_STATE)["pager"] in ("Showing all %d stocks" % len(exp), None), "pager says all are shown")
        badpe = [(x["symbol"], x["pe"]) for x in data if x["symbol"] in vis and not (isnum(x["pe"]) and 0 < x["pe"] <= 60)]
        check(not badpe, "every listed P/E is a positive number up to 60 (no text, missing or negative) " + str(badpe[:4]))
        pg.select_option("#fsSector", "Financials"); settle()
        want = [x for x in data if passes(x, dict(crit, sector="Financials"))]
        check(ev(JS_ALL) == order(want, "pe", True) and len(ev(JS_VIS)) == min(50, len(want)), "adding Sector Financials: %d rows in P/E order, back to the first page" % len(want))
        exp = order(want, "pe", True)

        # 3. the other two tabs list exactly the same stocks
        pg.click("#ftabs button:nth-child(2)"); settle()
        check(sorted(ev(JS_TAB, "t2a")) == sorted(exp) and sorted(ev(JS_TAB, "t2")) == sorted(exp), "Financial Health lists the same %d stocks" % len(exp))
        pg.click("#ftabs button:nth-child(3)"); settle()
        check(sorted(ev(JS_TAB, "t3a")) == sorted(exp), "Cash Flow & Growth lists the same stocks")
        pg.click("#ftabs button:nth-child(1)"); settle()

        # 4. negative P/E only, negative P/B only, a P/B range, an invalid box
        pg.click("#fsReset"); settle()
        pg.select_option("#fsPeMode", "negative"); settle()
        want = [x for x in data if passes(x, {"peMode": "negative"})]
        check(sorted(ev(JS_ALL)) == sorted(x["symbol"] for x in want), "Negative P/E only: the %d loss-making stocks" % len(want))
        pg.select_option("#fsPeMode", "any"); pg.select_option("#fsPbMode", "negative"); settle()
        want = [x for x in data if passes(x, {"pbMode": "negative"})]
        check(sorted(ev(JS_ALL)) == sorted(x["symbol"] for x in want), "Negative P/B only: the %d negative-book-value stocks" % len(want))
        pg.select_option("#fsPbMode", "any"); pg.fill("#fsPbMin", "2"); pg.fill("#fsPbMax", "6"); settle()
        want = [x for x in data if passes(x, {"pbMin": 2.0, "pbMax": 6.0})]
        check(sorted(ev(JS_ALL)) == sorted(x["symbol"] for x in want), "P/B 2 to 6: %d stocks, no negative or missing P/B" % len(want))
        pg.fill("#fsPbMax", "6x"); settle()
        check(ev(JS_ALL) == [] and pg.get_attribute("#fsPbMax", "aria-invalid") == "true", "an invalid box shows no rows and is marked invalid")
        pg.click("#fsReset"); settle()
        check(len(ev(JS_ALL)) == total and ev(JS_STATE)["count"] == "All %d stocks (no filter applied)." % total, "Clear filters: all %d again" % total)

        # 5. keyboard only
        pg.focus("#fsPeMax"); pg.keyboard.type("30"); settle()
        check(sorted(ev(JS_ALL)) == sorted(x["symbol"] for x in data if passes(x, {"peMax": 30.0})), "typing in the P/E box filters")
        pg.focus('#ftable [data-k="symbol"]'); pg.keyboard.press("Enter"); settle()
        check(ev(JS_ALL)[:3] == order([x for x in data if passes(x, {"peMax": 30.0})], "symbol", True)[:3], "Enter on a column button sorts")
        nxt = ev(JS_ALL)[50]
        pg.focus("#ftable + .fs-more button"); pg.keyboard.press("Enter"); settle()
        check(len(ev(JS_VIS)) == 100 and ev("()=>document.activeElement.textContent.trim()") == nxt, "Enter on Show more shows 50 more and moves focus to %s" % nxt)
        pg.focus("#fsReset"); pg.keyboard.press("Space"); settle()
        check(len(ev(JS_ALL)) == total, "Space on Clear filters clears them")
        ctx.close()

        # 6. phone controls
        ctx = b.new_context(viewport={"width": 390, "height": 900})
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
        pg = ctx.new_page(); pg.goto(base + "#screener"); pg.wait_for_timeout(1200)
        small = pg.evaluate("()=>[...document.querySelectorAll('#fscreen input,#fscreen select,#fscreen button,#ftable th .lk,#ftable + .fs-more button,#ftabs button')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height<44}).map(e=>e.id||e.textContent.trim())")
        check(not small, "390px: every filter, sort, tab and Show more control is at least 44px high " + str(small[:5]))
        ctx.close()
        b.close()
    srv.shutdown()
    check(not errors, "no page errors %s" % errors[:2])
    bad = [m for c, m in CHECKS if not c]
    print("\n%d checks, %d passed, %d failed (%d synthetic stocks)" % (len(CHECKS), len(CHECKS) - len(bad), len(bad), total))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 520))
