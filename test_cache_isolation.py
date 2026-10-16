"""Cache isolation: no workflow outside the stocklens-ledger concurrency group may save a cache that the publishing run ("Update data and publish site") or a
fundamentals batch would restore. Such a cache can be saved AFTER a batch saved its progress and then be restored as the newest one, rolling the batch
(and its gate state) back. The rule is checked on every workflow file, and the rollback is replayed with GitHub's restore rule: the newest cache whose key
starts with a restore prefix and whose path set is the same. Offline: reads the workflow files only.
Run: python3 -m unittest test_cache_isolation"""
import subprocess
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
WF = HERE / ".github" / "workflows"
LEDGER = "stocklens-ledger"
PRE_FIX = "4eb39f7e6ab7a81f81055d8e9081ff17d2f41a8f"     # the last commit before the side-* cache prefixes (the baseline the rollback test compares with)


def caches(doc):
    """[(job, kind, key, restore_prefixes, paths, group)] for every actions/cache step. kind: 'both' (restore + save), 'restore' or 'save'."""
    top = (doc.get("concurrency") or {}).get("group") if isinstance(doc.get("concurrency"), dict) else doc.get("concurrency")
    out = []
    for jname, job in (doc.get("jobs") or {}).items():
        conc = job.get("concurrency")
        group = (conc.get("group") if isinstance(conc, dict) else conc) or top
        for st in job.get("steps") or []:
            uses = str(st.get("uses") or "")
            if not uses.startswith("actions/cache"):
                continue
            kind = "save" if "/save@" in uses else "restore" if "/restore@" in uses else "both"
            w = st.get("with") or {}
            paths = frozenset(p.strip() for p in str(w.get("path", "")).splitlines() if p.strip())
            prefixes = [p.strip() for p in str(w.get("restore-keys", "")).splitlines() if p.strip()]
            out.append((jname, kind, str(w.get("key", "")), prefixes, paths, group))
    return out


def load_all(read):
    """{workflow file name: caches(...)} using read(name) -> text or None."""
    out = {}
    for name in sorted(p.name for p in WF.glob("*.yml")):
        text = read(name)
        if text is not None:
            out[name] = caches(yaml.safe_load(text))
    return out


def publishing_restores(all_caches):
    """The (prefix, paths) pairs the publishing run and the batch restore from."""
    want = set()
    for name in ("update.yml", "fundamentals_batch.yml"):
        for _, kind, key, prefixes, paths, _ in all_caches.get(name, []):
            if kind in ("both", "restore"):
                want |= {(p, paths) for p in prefixes}
    return want


def violations(all_caches):
    """Caches saved outside the ledger group that a publishing restore would pick up."""
    targets = publishing_restores(all_caches)
    bad = []
    for name, cs in all_caches.items():
        for job, kind, key, _, paths, group in cs:
            if kind in ("both", "save") and group != LEDGER and any(key.startswith(p) and paths == ps for p, ps in targets):
                bad.append((name, job, key, group))
    return bad


def replay(all_caches, side, match_paths=True):
    """A batch saves its progress while a run of the side workflow (another concurrency group) is in flight and saves after it. Which saved data does
    the next publishing run restore? GitHub restores the newest cache whose key starts with the restore prefix AND whose path set is the same (the
    cache version is derived from the paths), so a side cache with a different path set is never picked."""
    def saved(name):
        for _, kind, key, _, paths, _ in all_caches[name]:
            if kind in ("both", "save"):
                return key.replace("${{ github.run_id }}", ""), paths
        raise AssertionError("no saved cache in " + name)
    (k0, p0), (k1, p1), (k2, p2) = saved("update.yml"), saved("fundamentals_batch.yml"), saved(side)
    store = [(0, k0 + "100", p0, "before-batch"), (2, k1 + "101", p1, "batch-progress"), (3, k2 + "102", p2, "side-run (restored before the batch saved)")]
    (prefix, paths), = publishing_restores(all_caches)
    return max(c for c in store if c[1].startswith(prefix) and (c[2] == paths or not match_paths))[3]


class Isolation(unittest.TestCase):
    def setUp(self):
        self.now = load_all(lambda n: (WF / n).read_text(encoding="utf-8"))

    def test_the_publishing_run_and_the_batch_restore_the_same_saved_data(self):
        self.assertEqual(publishing_restores(self.now), {("nse-data-", frozenset({"data", "out"}))})

    def test_no_cache_saved_outside_the_ledger_group_can_be_restored_by_them(self):
        self.assertEqual(violations(self.now), [])

    def test_the_side_workflows_use_their_own_prefixes(self):
        keys = {n: [k for _, kind, k, _, _, _ in cs if kind in ("both", "save")] for n, cs in self.now.items()}
        self.assertEqual(keys["historical.yml"], ["side-historical-${{ github.run_id }}"])
        self.assertEqual(keys["market_data.yml"], ["side-market-${{ github.run_id }}"])
        self.assertTrue(all(not k.startswith("nse-data-") for k in keys["sector_data.yml"]))

    def test_a_batch_is_not_rolled_back_by_any_side_workflow(self):
        for side in ("historical.yml", "market_data.yml", "financial_history.yml", "shareholding.yml"):
            if side in ("financial_history.yml", "shareholding.yml"):
                self.assertEqual({c[5] for c in self.now[side]}, {LEDGER}, side)       # same group as the batch: they never run at the same time
                continue
            self.assertEqual(replay(self.now, side), "batch-progress", side)

    def test_the_side_prefixes_protect_the_batch_even_without_the_path_rule(self):
        for side in ("historical.yml", "market_data.yml"):                     # GitHub documents the prefix rule; the path-set rule is only implied
            self.assertEqual(replay(self.now, side, match_paths=False), "batch-progress", side)

    def test_the_workflows_before_the_fix_show_the_rollback_it_prevents(self):
        def before_fix(n):
            r = subprocess.run(["git", "show", PRE_FIX + ":.github/workflows/" + n], cwd=str(HERE), capture_output=True, text=True)
            return r.stdout if r.returncode == 0 else None
        old = load_all(before_fix)
        if "update.yml" not in old:
            self.skipTest("commit %s is not available in this checkout" % PRE_FIX[:7])
        old["fundamentals_batch.yml"] = self.now["fundamentals_batch.yml"]        # the batch as it would have run on top of the old workflows
        self.assertEqual([(v[0], v[2]) for v in violations(old)], [("market_data.yml", "nse-data-market-${{ github.run_id }}")])
        self.assertEqual(replay(old, "market_data.yml"), "side-run (restored before the batch saved)")   # the batch's progress would be lost
        self.assertEqual(replay(old, "historical.yml", match_paths=False), "side-run (restored before the batch saved)")  # unless the path rule saves it
        self.assertEqual(replay(self.now, "market_data.yml"), "batch-progress")  # and the current workflows keep it

if __name__ == "__main__":
    unittest.main()
