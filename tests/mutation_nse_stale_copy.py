"""
Mutation test for the NSE stale-copy handling (nse_dates.py, nse_updater.download_missing, market_data_updater gap reporting).   Run from the repository root:
    python3 tests/mutation_nse_stale_copy.py
Each mutant is ONE deliberate change, applied to a COPY of the code in a temporary folder; test_nse_stale_copy.py (with the existing market-data tests as fixtures) must fail for it.
Exit 1 if any mutant survives or its pattern is not found.
"""
import concurrent.futures as cf
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D, U, M_ = "nse_dates.py", "nse_updater.py", "market_data_updater.py"
M = [
    (D, "a stale copy is classified as a match", "        return \"stale_copy\", next(iter(days))", "        return \"match\", None"),
    (D, "mixed earlier days are treated as one stale copy", "len(days) == 1 and next(iter(days)) < req", "min(days) < req"),
    (D, "a later date counts as a stale copy", "len(days) == 1 and next(iter(days)) < req", "len(days) == 1 and next(iter(days)) != req"),
    (D, "unreadable dates are ignored", "if not found or any(f is None for f in found):", "found = [f for f in found if f is not None]\n    if not found:"),
    (D, "the 3-day boundary moves", "> RECENT_DAYS", ">= RECENT_DAYS"),
    (D, "any recent day counts as old", "> RECENT_DAYS", "> 0"),
    (D, "the list accepts a served day that is not earlier", "        if srv < req:", "        if True:"),
    (U, "a stale copy is saved under the requested day", "            if verdict == \"match\":", "            if verdict in (\"match\", \"stale_copy\"):"),
    (U, "every response is saved", "            if verdict == \"match\":", "            if True:"),
    (U, "a recent stale copy is recorded as a holiday", "                if nd.old_enough(d.isoformat(), today):", "                if True:"),
    (U, "a stale copy is never recorded", "                    stale[d.isoformat()] = served", "                    pass"),
    (U, "a recorded stale day is asked for again", " or d.isoformat() in stale:", ":"),
    (U, "the stale list is never written", "    if stale != stale_before:", "    if False:"),
    (U, "a stale day is also turned into an ordinary holiday", "                    stale[d.isoformat()] = served", "                    stale[d.isoformat()] = served\n                    known_holidays.add(d.isoformat())"),
    (U, "the 404 holiday rule changes", "elif (today - d).days > 3:", "elif (today - d).days > 0:"),
    (U, "the day is not passed to the classifier", "nd.classify(nd.response_dates(r.content), d)", "nd.classify(nd.response_dates(r.content), d - dt.timedelta(days=1))"),
    (M_, "a day with a good file can still be called a holiday", "    stale_copy -= set(days)", "    pass"),
    (M_, "a recent stale copy is counted as a holiday", "r.get(\"date\") and nd.old_enough(r[\"date\"], now.date())}", "r.get(\"date\")}"),
    (M_, "stale holidays hide every gap", "    gaps = calendar_gaps(list(days), set(holidays) | stale_copy)", "    gaps = []"),
    (M_, "stale copies do not explain a gap", "    gaps = calendar_gaps(list(days), set(holidays) | stale_copy)", "    gaps = calendar_gaps(list(days), set(holidays))"),
    (M_, "the recorded list is ignored", "stale_days=set(nd.read_stale(Path(root) / \"data\" / nd.STALE_FILE_NAME))", "stale_days=set()"),
    (M_, "the holiday summary warning is dropped", "    if stale_copy:\n        warnings.append(", "    if False:\n        warnings.append("),
    (M_, "the holiday days are not published in the quality block", "\"stale_copy_days\": sorted(stale_copy),", "\"stale_copy_days\": [],"),
    (M_, "the stale-copy mark is lost", "stale_copy_of=served if verdict == \"stale_copy\" else None", "stale_copy_of=None"),
]


def run(i, m):
    f, name, old, new = m
    src = (ROOT / f).read_text()
    if src.count(old) < 1:
        return i, name, "PATTERN NOT FOUND"
    tmp = Path(tempfile.mkdtemp())
    try:
        for p in ROOT.glob("*.py"):
            shutil.copy(p, tmp / p.name)
        (tmp / f).write_text(src.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "-f", "test_nse_stale_copy"], cwd=tmp, capture_output=True, text=True, timeout=900)
        return i, name, "killed" if r.returncode != 0 else "SURVIVED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        results = sorted(ex.map(lambda a: run(a[0], a[1]), enumerate(M)))
    bad = 0
    for i, name, res in results:
        print("%-16s %s" % (res, name))
        bad += res != "killed"
    print("\n%d mutants, %d killed, %d not killed" % (len(M), len(M) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
