"""
The responsive redesign of index.html is recorded edit by edit in tests/approved_page_edits.json ("decision_edits", see tests/page_edit_manifest.py).
These tests keep that record honest: it must be exactly the difference between the decision base page and index.html, reversing it must give the base
page back byte for byte, and any change that is NOT in it must still be seen by the guards that every older pin relies on.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
import page_edit_manifest as M          # noqa: E402
import guard_update_yml as G            # noqa: E402

DOC = json.loads((ROOT / "tests" / "approved_page_edits.json").read_text(encoding="utf-8"))
PAGE = (ROOT / "index.html").read_text(encoding="utf-8")


def have_base():
    return subprocess.run(["git", "cat-file", "-e", M.BASE_COMMIT + "^{commit}"], cwd=str(ROOT), capture_output=True).returncode == 0


@unittest.skipUnless(have_base(), "the decision base commit is not in this clone's history")
class DecisionEditsRecord(unittest.TestCase):
    def setUp(self):
        self.base = M.base_page()
        self.edits = DOC["decision_edits"]

    def test_record_is_exactly_the_page_difference(self):
        self.assertEqual(DOC["decision_base_commit"], M.BASE_COMMIT)
        self.assertEqual(self.edits, M.build(self.base, PAGE), "re-record with tests/page_edit_manifest.py --write after a reviewed edit")
        self.assertEqual(DOC["decision_sha256"], M.digest(self.edits))

    def test_reversal_gives_the_base_page_back(self):
        self.assertEqual(M.reverse(PAGE, self.edits), self.base)
        self.assertEqual(M.reverse(self.base, self.edits), self.base, "a page without the edits is returned unchanged")
        for e in self.edits:
            self.assertEqual(self.base.count(e["new"]), 0, "no recorded new text already occurs in the base page")
        for commit in ("aa4ace1", "4eb39f7"):                                    # pages the older pins compare against
            r = subprocess.run(["git", "show", commit + ":index.html"], cwd=str(ROOT), capture_output=True)
            if r.returncode == 0:
                older = r.stdout.decode("utf-8")
                self.assertEqual(G.without_coverage_fixes(older), M.reverse(G.without_coverage_fixes(older), self.edits))
                self.assertEqual(M.reverse(older, self.edits), older, commit + " carries none of the edits")
        for e in self.edits:
            self.assertEqual(PAGE.count(e["new"]), 1)
            self.assertTrue(e["new"], "every recorded edit has a non-empty new text")

    def test_guards_reverse_the_decision_first_and_still_see_anything_else(self):
        self.assertEqual(G.without_coverage_fixes(PAGE), G.without_coverage_fixes(self.base))
        self.assertEqual(G.without_market_cap(PAGE), G.without_market_cap(self.base))
        self.assertNotEqual(G.without_market_cap(PAGE + "<!-- x -->"), G.without_market_cap(self.base), "an unrecorded addition is still a difference")
        with self.assertRaises(ValueError):
            G.without_coverage_fixes(PAGE + self.edits[0]["new"])        # a recorded edit twice: an error, nothing removed on a guess
        partial = PAGE.replace(self.edits[-1]["new"], self.edits[-1]["old"], 1)
        with self.assertRaises(ValueError):
            G.without_coverage_fixes(partial)                             # only some of the recorded edits: an error
        e = next(x for x in self.edits if "max-width" in x["new"])
        mutated = PAGE.replace(e["new"], e["new"].replace("max-width", "max-widht", 1), 1)
        with self.assertRaises(ValueError):
            G.without_coverage_fixes(mutated)                             # a recorded edit changed afterwards: no longer the approved text

    def test_the_coverage_edits_are_untouched(self):
        old = json.loads(subprocess.run(["git", "show", M.BASE_COMMIT + ":tests/approved_page_edits.json"], cwd=str(ROOT), capture_output=True, check=True).stdout)
        for k, v in old.items():
            self.assertEqual(DOC[k], v, "key %s of the manifest is unchanged" % k)
        self.assertEqual(sorted(set(DOC) - set(old)), ["decision_base_commit", "decision_edits", "decision_note", "decision_sha256"])


if __name__ == "__main__":
    unittest.main()
