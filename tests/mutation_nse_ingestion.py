"""
Mutation test for the raw NSE file ingestion fix in nse_updater.py.   Run from the repository root:   python3 tests/mutation_nse_ingestion.py
Each mutant is ONE deliberate change, applied to a COPY of the code in a temporary folder; test_nse_ingestion.py must fail for it. Exit 1 if any survives.
"""
import concurrent.futures as cf
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
U = "nse_updater.py"
M = [
    ("misdated file accepted", "if off.any():", "if False:"),
    ("only the first row is checked", "off = days != pd.Timestamp(name_date)", "off = (days != pd.Timestamp(name_date)) & (days.index == 0)"),
    ("unreadable DATE1 not rejected", "days = pd.to_datetime(d.DATE1, format=\"%d-%b-%Y\", errors=\"coerce\")", "days = pd.to_datetime(d.DATE1, format=\"%d-%b-%Y\", errors=\"coerce\").fillna(pd.Timestamp(name_date))"),
    ("file name check removed", "if name_date is None:\n        return None, \"the file name is not bhav_YYYYMMDD.csv\"", "if name_date is None:\n        name_date = dt.datetime.strptime(pd.read_csv(f, skipinitialspace=True).iloc[0, 2].strip(), \"%d-%b-%Y\").date()"),
    ("rejected file still concatenated", "        else:\n            frames.append(d)", "        if d is not None:\n            frames.append(d)\n        else:\n            frames.append(pd.read_csv(f, skipinitialspace=True).rename(columns=lambda c: c.strip()).assign(SERIES=\"EQ\"))"),
    ("rejection not recorded", "rejected.append({\"file\": f.name, \"reason\": why})", "pass"),
    ("rejection not logged", "log(\"REJECTED raw file\", f.name, \"-\", why, \"- not used as a trading day\")", "pass"),
    ("all rejected does not stop", "if not frames:\n        sys.exit(", "if False:\n        sys.exit("),
    ("identical repeats kept", "df = df.drop_duplicates()", "pass"),
    ("conflict not detected", "if len(clash):", "if False:"),
    ("conflict resolved by keeping last", "if len(clash):\n        ex =", "if len(clash):\n        return df.drop_duplicates([\"SYMBOL\", \"DATE\"], keep=\"last\").sort_values([\"SYMBOL\", \"DATE\"]).reset_index(drop=True)\n        ex ="),
    ("repeat compared on close only", "df = df.drop_duplicates()", "df = df.drop_duplicates([\"SYMBOL\", \"DATE\", \"CLOSE_PRICE\"])"),
    ("non-EQ rows included", "df = df[df.SERIES == \"EQ\"].copy()", "df = df.copy()"),
    ("EQ filter checks the wrong series", "df = df[df.SERIES == \"EQ\"].copy()", "df = df[df.SERIES != \"BE\"].copy()"),
    ("series not stripped", "for c in (\"SYMBOL\", \"SERIES\", \"DATE1\"):\n        d[c] = d[c].astype(str).str.strip()", "pass"),
    ("report counts wrong", "\"files_used\": len(frames)", "\"files_used\": len(files)"),
    ("scan threshold changed", "MIN_TURNOVER_LACS = 100", "MIN_TURNOVER_LACS = 101"),
    ("indicator formula changed", "x.rolling(50).mean()", "x.rolling(49).mean()"),
    ("return window changed", "pct_change(63)", "pct_change(62)"),
]


def run(i, m):
    name, old, new = m
    src = (ROOT / U).read_text()
    if old not in src:
        return i, name, "PATTERN NOT FOUND"
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copy(ROOT / "test_nse_ingestion.py", tmp / "test_nse_ingestion.py")
        (tmp / U).write_text(src.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "-f", "test_nse_ingestion"], cwd=tmp, capture_output=True, text=True, timeout=600)
        return i, name, "killed" if r.returncode != 0 else "SURVIVED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        results = sorted(ex.map(lambda a: run(a[0], a[1]), enumerate(M)))
    bad = 0
    for i, name, res in results:
        print("%-9s %s" % (res, name))
        bad += res != "killed"
    print("\n%d mutants, %d killed, %d not killed" % (len(M), len(M) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
