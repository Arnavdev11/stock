"""
COMBINED preview site for local browser checks of index.html: every section populated at once. Not run by CI.

    python tests/hybrid_preview_site.py <empty-output-dir> [data-ref]

Starts from tests/readonly_data_site.py (a READ-ONLY copy of already-published real data: prices, shareholding, financial history, stock
directory, company profiles, growth events) and adds, ONLY for the files that are not in git:
  * SYNTHETIC fundamentals.json, financials.json, scans.json and fiidii_history.json from tests/synthetic_site.py (invented values, marked synthetic);
  * a FIXTURE Quarterly Results file for TCS, built by quarterly_results_updater.build_outputs() from the response fixture in
    test_quarterly_results.py (the verified real response shape, with invented numbers).
MANIFEST.json says which file is real, synthetic or fixture. Nothing is fetched and nothing is written inside this repository's out/ or data/.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import readonly_data_site      # noqa: E402
import synthetic_site          # noqa: E402

SYNTHETIC_FILES = ("fundamentals.json", "financials.json", "scans.json", "fiidii_history.json")


def build(dest, ref="origin/stocklens-data"):
    dest = Path(dest).resolve()
    commit, used = readonly_data_site.build(dest, ref)
    syn = dest.parent / (dest.name + ".synthetic-tmp")
    synthetic_site.build(syn)
    for name in SYNTHETIC_FILES:
        (dest / "out" / name).write_bytes((syn / "out" / name).read_bytes())
        used["out/" + name] = "SYNTHETIC: tests/synthetic_site.py (invented values)"
    for p in sorted(syn.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    syn.rmdir()
    import test_quarterly_results as t           # the updater's own test fixture
    import quarterly_results_updater as q
    rec = q.empty_record()
    q.merge_quarters(rec, q.parse_quarterly(t.REAL), "consolidated", "2026-08-01")
    rec["last_checked"] = rec["last_success"] = "2026-08-01"
    entry = {"symbol": "TCS", "isin": "INE467B01029", "name": "Tata Consultancy Services Limited"}
    docs, _summary, problems = q.build_outputs({"TCS": entry}, {"TCS": rec}, "2026-08-01")
    qdir = dest / "out" / "by_symbol" / "quarterly_results"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "TCS.json").write_text(json.dumps(docs["TCS"]), encoding="utf-8")
    used["out/by_symbol/quarterly_results/TCS.json"] = "FIXTURE: quarterly_results_updater.build_outputs() on test_quarterly_results.REAL (real response shape, invented numbers); problems=%s" % problems
    man = json.loads((dest / "MANIFEST.json").read_text())
    man.update({"kind": "combined preview: read-only real copy + synthetic and fixture files where no real file is in git", "files": used,
                "absent": [x for x in man["absent"] if x.split("/")[-1] not in SYNTHETIC_FILES and "quarterly" not in x]})
    (dest / "MANIFEST.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    return commit, used


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    c, u = build(*sys.argv[1:])
    print("data commit", c)
    print("\n".join("%s <- %s" % kv for kv in u.items()))
