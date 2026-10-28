"""
Real-browser checks of the stock-page tables on phones and desktop (Financial History, shareholding quarters, Quarterly Results, Investment
Checklist), on the COMBINED preview (tests/hybrid_preview_site.py: read-only real copy + labelled synthetic/fixture files). Not run by CI.

    python tests/browser_mobile_tables_checks.py <new-scratch-dir>

Phones (320-430px): every table is a card list; no table is wider than its box; every non-empty cell is shown, inside its card and inside the
screen, each card is headed by its row's first cell (the measure, quarter or item), and every other value cell carries its column
name (data-label, drawn before the value). Desktop (768px and wider): the same tables are ordinary
tables with their header row and the same rows and cell texts as on a phone (nothing added or dropped). Light and dark. Exit 0 only if all pass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hybrid_preview_site      # noqa: E402
from browser_checks import serve  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PHONES, DESKTOPS = [320, 360, 375, 390, 430], [768, 1280]
TABLES = ["#finHistTable", "#shTable", "#detailQuarterly table", "#detailChecklist table"]
PROBE = r"""
(sels) => sels.map((sel) => { const ts = [...document.querySelectorAll(sel)];
  return ts.map((t) => { const vw = document.documentElement.clientWidth, P = t.parentElement.getBoundingClientRect(), T = t.getBoundingClientRect();
    const heads = [...t.querySelectorAll("thead th")].map((h) => h.textContent.trim()), cells = [];
    [...t.querySelectorAll("tbody tr")].forEach((tr) => [...tr.children].forEach((td, i) => { const txt = td.textContent.replace(/\s+/g, " ").trim(); if (!txt) return;
      const r = td.getBoundingClientRect(), card = tr.getBoundingClientRect(), before = getComputedStyle(td, "::before").content;
      cells.push({ txt: txt.slice(0, 40), shown: r.width > 0 && r.height > 0, inCard: r.left >= card.left - 1 && r.right <= card.right + 1, inScreen: r.left >= -1 && r.right <= vw + 1,
        title: i === 0 && !td.hasAttribute("colspan"), label: td.getAttribute("data-label"), wantLabel: i === 0 || td.hasAttribute("colspan") || !heads[i] ? null : heads[i], before,
        titleOk: i === 0 && !td.hasAttribute("colspan") ? (r.top <= Math.min(...[...tr.children].slice(1).map((x) => x.getBoundingClientRect().top || 1e9)) + 1 && +getComputedStyle(td).fontWeight >= 600) : true }); }));
    return { sel, display: getComputedStyle(t).display, theadShown: getComputedStyle(t.tHead || t).display !== "none", fits: T.width <= P.width + 1, rows: t.tBodies[0].rows.length, cells }; }); }).flat()
"""
CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    if not cond:
        print("FAIL " + msg)


def main(scratch):
    scratch = Path(scratch)
    site = scratch / "tables-site"
    if site.exists():
        raise SystemExit("use a new scratch directory")
    hybrid_preview_site.build(site)
    srv = serve(site)
    base = "http://127.0.0.1:%d/index.html#stock=TCS" % srv.server_address[1]
    errors, seen = [], {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for scheme in ("light", "dark"):
            for w in PHONES + DESKTOPS:
                ctx = b.new_context(viewport={"width": w, "height": 900}, color_scheme=scheme)
                ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(base)
                pg.wait_for_timeout(2500)
                res = pg.evaluate(PROBE, TABLES)
                got = {}
                for t in res:
                    got.setdefault(t["sel"], []).append(t)
                for sel in TABLES:
                    check(sel in got, "%s %dpx: %s is on the page" % (scheme, w, sel))
                    for t in got.get(sel, []):
                        tag = "%s %dpx %s" % (scheme, w, sel)
                        texts = [c["txt"] for c in t["cells"]]
                        seen.setdefault(sel, {})[(scheme, w)] = (t["rows"], texts)
                        check(t["cells"], tag + ": has data")
                        check(all(c["shown"] for c in t["cells"]), tag + ": every non-empty cell is shown")
                        if w in PHONES:
                            check(t["display"] == "block" and not t["theadShown"], tag + ": card layout (display %s)" % t["display"])
                            check(t["fits"], tag + ": the table fits its box (no sideways scroll)")
                            check(all(c["inCard"] and c["inScreen"] for c in t["cells"]), tag + ": every value is inside its card and the screen " + str([c["txt"] for c in t["cells"] if not (c["inCard"] and c["inScreen"])][:3]))
                            lab = [c for c in t["cells"] if c["wantLabel"]]
                            check(all(c["label"] == c["wantLabel"] and c["wantLabel"] in c["before"] for c in lab), tag + ": every value shows its column name (%d labelled)" % len(lab))
                            check(all(c["titleOk"] for c in t["cells"] if c["title"]), tag + ": each card is headed by its row's first cell (the measure, quarter or item), bold and above the values")
                        else:
                            check(t["display"] == "table" and t["theadShown"], tag + ": an ordinary table with its header row on desktop")
                ctx.close()
        b.close()
    srv.shutdown()
    for sel, by in seen.items():
        ref = by.get(("light", 1280))
        check(ref and all(v == ref for v in by.values()), "%s: the same rows and cell texts at every width and theme (%d rows, %d cells)" % (sel, ref[0] if ref else 0, len(ref[1]) if ref else 0))
    check(not errors, "no page errors %s" % errors[:2])
    bad = [m for c, m in CHECKS if not c]
    print("%d checks, %d passed, %d failed" % (len(CHECKS), len(CHECKS) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
