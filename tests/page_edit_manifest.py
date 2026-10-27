"""
The approved responsive-redesign edits to index.html, recorded as "decision_edits" in tests/approved_page_edits.json.

    python tests/page_edit_manifest.py            # check: the recorded edits are exactly the difference between the base page and index.html
    python tests/page_edit_manifest.py --write    # (re)record them after an intentional, reviewed edit to index.html

Each entry is the exact new text and the exact text it replaced in the decision base commit. guard_update_yml.without_coverage_fixes()
and tests/legacy_5i.js reverse "decision_edits" first (every new text must occur exactly once; a page with none of them comes back
unchanged; a page with only some of them, or one twice, is an error), so every older byte-identity pin keeps proving that NOTHING ELSE
in the page changed. This tool only writes the decision_* keys; the coverage "edits" and every other key are kept exactly as they are.
"""
import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "approved_page_edits.json"
BASE_COMMIT = "66fe34a"          # origin/main when the responsive redesign was approved (2026-10-27)
NOTE = ("Approved index.html edits of the responsive redesign (English only): wider page and single-row global bar with a full-width search placeholder, "
        "WCAG AA gain/loss colours and dark-mode selected states, 24px/44px touch targets, the filter-first Fundamental screen (P/E, P/B, ROE, Sector; strict numbers; "
        "negative P/E only on request), the All-Time High / Low and Sector Leaders / Laggards blocks removed from the home page (their data and code are untouched), and "
        "mobile table cards. Each entry is the exact new text and the exact text it replaced in " + BASE_COMMIT + ". Reversed BEFORE the coverage edits by "
        "guard_update_yml.without_coverage_fixes() and tests/legacy_5i.js. Recorded with tests/page_edit_manifest.py --write; changing these edits again means re-recording them on purpose.")


def base_page(commit=BASE_COMMIT):
    return subprocess.run(["git", "show", commit + ":index.html"], cwd=str(ROOT), capture_output=True, check=True).stdout.decode("utf-8")


def count(page, text):
    return len(page.split(text)) - 1


def reverse(page, edits):
    """The guards' rule, exactly: all present once, or none present; then each new text is replaced by its old text, in order."""
    n = [count(page, e["new"]) for e in edits]
    if not any(n):
        return page
    if any(x != 1 for x in n):
        raise ValueError("decision edits: each approved edit must occur exactly once, found %s" % n)
    for e in edits:
        page = page.replace(e["new"], e["old"], 1)
    return page


def build(old, new):
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            hunks.append([i1, i2, j1, j2])
    out = []
    for h in hunks:
        i1, i2, j1, j2 = h
        while True:                                    # widen with unchanged neighbouring lines (both sides) until the new text is non-empty,
            text = "".join(b[j1:j2])                   # occurs once in the page, and does not occur at all in the base page (so a page without the edits is left unchanged)
            if text and count(new, text) == 1 and count(old, text) == 0:
                break
            grew = False
            if j1 > 0 and i1 > 0 and a[i1 - 1] == b[j1 - 1]:
                i1, j1, grew = i1 - 1, j1 - 1, True
            if j2 < len(b) and i2 < len(a) and a[i2] == b[j2]:
                i2, j2, grew = i2 + 1, j2 + 1, True
            if not grew:
                raise ValueError("cannot make an edit unique near line %d" % (j1 + 1))
        if out and j1 <= out[-1][3]:                   # touching or overlapping the previous edit: one edit
            p = out[-1]
            out[-1] = [p[0], max(p[1], i2), p[2], max(p[3], j2)]
        else:
            out.append([i1, i2, j1, j2])
    edits = [{"new": "".join(b[j1:j2]), "old": "".join(a[i1:i2])} for i1, i2, j1, j2 in out]
    if [count(new, e["new"]) for e in edits] != [1] * len(edits):
        raise ValueError("an edit does not occur exactly once")
    if any(count(old, e["new"]) for e in edits):
        raise ValueError("an edit's new text already occurs in the base page")
    if reverse(new, edits) != old:
        raise ValueError("reversing the edits does not give back the base page")
    return edits


def digest(edits):
    return hashlib.sha256(json.dumps(edits, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def main(argv):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    base = base_page()
    edits = build(base, page)
    if "--write" in argv:
        doc["decision_note"] = NOTE
        doc["decision_base_commit"] = BASE_COMMIT
        doc["decision_edits"] = edits
        doc["decision_sha256"] = digest(edits)
        MANIFEST.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")   # the file's own format: no trailing newline
        print("recorded %d decision edits (%s)" % (len(edits), doc["decision_sha256"][:12]))
        return 0
    ok = doc.get("decision_edits") == edits and doc.get("decision_sha256") == digest(edits)
    print("decision edits %s (%d recorded, %d in the page difference)" % ("match" if ok else "DO NOT MATCH", len(doc.get("decision_edits") or []), len(edits)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
