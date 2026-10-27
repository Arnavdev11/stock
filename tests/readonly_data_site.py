"""
READ-ONLY copy of already-published real data, assembled into a local preview site for browser checks of index.html. Not run by CI.

    python tests/readonly_data_site.py <empty-output-dir> [data-ref]      (data-ref defaults to origin/stocklens-data)

Reads, with `git show` only (no fetch, no network, nothing written to any branch), the files the daily workflow itself copies from the data
branch into the site (historical/historical.json, historical/shards/*.json, stock_directory.json) plus the committed company_profiles.json,
and builds out/financial_history.json and out/shareholding.json from the two ledgers with the updaters' own offline build_output() functions
(no provider, no token). It refuses to write inside this repository's out/ or data/ folders.

Files the workflow publishes from its Actions cache (fundamentals, financials, quarterly results, scans and market files) are NOT in git, so
they are absent here and every part of the page that needs them shows its normal "not loaded" state. MANIFEST.json lists what was used.
"""
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def git_show(ref, path):
    return subprocess.run(["git", "show", "%s:%s" % (ref, path)], cwd=str(ROOT), capture_output=True, check=True).stdout


def build(dest, ref="origin/stocklens-data"):
    dest = Path(dest).resolve()
    for bad in (ROOT / "out", ROOT / "data"):
        if dest == bad or bad in dest.parents:
            raise SystemExit("refusing to write inside %s" % bad)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("output directory must be empty or new: %s" % dest)
    commit = subprocess.run(["git", "rev-parse", ref], cwd=str(ROOT), capture_output=True, text=True, check=True).stdout.strip()
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref], cwd=str(ROOT), capture_output=True, text=True, check=True).stdout.split()
    out = dest / "out"
    (out / "by_symbol" / "historical").mkdir(parents=True)
    shutil.copyfile(ROOT / "index.html", dest / "index.html")
    used = {}
    (out / "historical.json").write_bytes(git_show(ref, "historical/historical.json")); used["out/historical.json"] = "copied: %s historical/historical.json" % ref
    for p in [x for x in listing if x.startswith("historical/shards/") and x.endswith(".json")]:
        (out / "by_symbol" / "historical" / Path(p).name).write_bytes(git_show(ref, p))
    used["out/by_symbol/historical/*.json"] = "copied: %s historical/shards/" % ref
    (out / "stock_directory.json").write_bytes(git_show(ref, "stock_directory.json")); used["out/stock_directory.json"] = "copied: %s stock_directory.json" % ref
    shutil.copyfile(ROOT / "company_profiles.json", out / "company_profiles.json"); used["out/company_profiles.json"] = "copied: committed company_profiles.json"
    import financial_history_updater as FH          # imported only for its offline build_output(); main() (the provider run) is never called
    import shareholding_updater as SH
    import universe
    day = dt.date.today().isoformat()                  # the updaters pass the day as an ISO string
    problems = {}
    fh = FH.build_output(json.loads(git_show(ref, "financial_history_ledger.json")), [], [], day)
    problems["financial_history"] = FH.validate_doc(fh)
    core = dict(fh, stocks={k: v for k, v in fh["stocks"].items() if k in universe.TEST_SYMBOLS})      # as the updater writes it: the 10 development stocks
    (out / "financial_history.json").write_text(json.dumps(core), encoding="utf-8"); used["out/financial_history.json"] = "built offline: financial_history_updater.build_output(%s financial_history_ledger.json), 10 development stocks" % ref
    led = json.loads(git_show(ref, "shareholding_ledger.json"))
    sh = SH.build_output(led, {}, day, [x for x in led["stocks"]])
    problems["shareholding"] = SH.validate_doc(sh)
    core_stocks = {k: v for k, v in sh["stocks"].items() if k in universe.TEST_SYMBOLS}
    (out / "shareholding.json").write_text(json.dumps(dict(sh, stocks=core_stocks, summary=SH.build_output(led, {}, day, list(core_stocks))["summary"])), encoding="utf-8")
    used["out/shareholding.json"] = "built offline: shareholding_updater.build_output(%s shareholding_ledger.json), 10 development stocks" % ref
    used["validation problems (validate_doc)"] = problems
    (dest / "MANIFEST.json").write_text(json.dumps({"kind": "read-only copy of published data", "ref": ref, "commit": commit, "files": used,
        "absent": ["out/fundamentals.json", "out/financials.json", "out/by_symbol/quarterly_results/", "out/scans.json", "out/market_*.json", "out/fiidii_history.json", "out/sensex_history.json"]}, indent=1), encoding="utf-8")
    return commit, used


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    c, u = build(*sys.argv[1:])
    print("data commit", c)
    print("\n".join("%s <- %s" % kv for kv in u.items()))
