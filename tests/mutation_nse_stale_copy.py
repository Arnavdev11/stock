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
    # classification and decoding
    (D, "a stale copy is classified as a match", "        return \"stale_copy\", next(iter(days))", "        return \"match\", None"),
    (D, "mixed earlier days are treated as one stale copy", "len(days) == 1 and next(iter(days)) < req", "min(days) < req"),
    (D, "a later date counts as a stale copy", "len(days) == 1 and next(iter(days)) < req", "len(days) == 1 and next(iter(days)) != req"),
    (D, "unreadable dates are ignored", "if not found or any(f is None for f in found):", "found = [f for f in found if f is not None]\n    if not found:"),
    (D, "the BOM is not removed", "b[len(codecs.BOM_UTF8):].decode(\"utf-8\")", "b.decode(\"utf-8\")"),
    (D, "UTF-16 is not supported", "elif b.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):", "elif False:"),
    (D, "UTF-32 is not supported", "if b.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):", "if False:"),
    (D, "Windows-1252 is not supported", "b.decode(\"cp1252\"), \"cp1252\"", "b.decode(\"ascii\"), \"cp1252\""),
    (D, "undefined bytes are decoded anyway (latin-1)", "b.decode(\"cp1252\"), \"cp1252\"", "b.decode(\"latin-1\"), \"cp1252\""),
    (D, "NUL bytes are accepted", "    return (text, enc) if \"\\x00\" not in text else (None, None)", "    return text, enc"),
    (D, "NUL in a str is accepted", "return (content, \"str\") if \"\\x00\" not in content else (None, None)", "return content, \"str\""),
    (D, "the 3-day boundary moves", "> RECENT_DAYS", ">= RECENT_DAYS"),
    (D, "any recent day counts as old", "> RECENT_DAYS", "> 0"),
    # the persisted observations
    (D, "a record with the wrong number of fields is parsed", "        if len(parts) != 5:", "        if False:"),
    (D, "dates need not be strict YYYY-MM-DD", "    if not _ISO.match(text):", "    if False:"),
    (D, "checks are not bounded", "        if not 1 <= checks <= 99:", "        if False:"),
    (D, "a weekend requested day is accepted", "        elif req.weekday() >= 5:", "        elif False:"),
    (D, "a weekend served day is accepted", "        elif srv.weekday() >= 5:", "        elif False:"),
    (D, "a served day that is not earlier is accepted", "        elif not srv < req:", "        elif False:"),
    (D, "an implausible gap is accepted", "        elif (req - srv).days > MAX_GAP_DAYS:", "        elif False:"),
    (D, "first_seen after last_checked is accepted", "        elif not first <= last:", "        elif False:"),
    (D, "a record first seen too soon is accepted", "        elif (first - req).days <= RECENT_DAYS:", "        elif False:"),
    (D, "a future last_checked is accepted", "        elif today is not None and last > today:", "        elif False:"),
    (D, "a duplicate day is accepted", "        elif req.isoformat() in records:", "        elif False:"),
    (D, "problems are dropped silently", "            problems.append((n, why))\n            continue", "            continue"),
    (D, "an unreadable file is silently empty", "        return records, [(0, \"the file is not readable text; none of it was used\")]", "        return records, []"),
    (D, "an observation is re-asked forever", "record[\"checks\"] < MAX_STALE_CHECKS and", "True and"),
    (D, "the re-check boundary moves", ">= REVALIDATE_AFTER_DAYS", "> REVALIDATE_AFTER_DAYS"),
    (D, "an observation is always due", ">= REVALIDATE_AFTER_DAYS", ">= 0"),
    (D, "a different answer does not start again", "    if old and old[\"served\"] == served_iso:", "    if old:"),
    (D, "a repeated answer adds no check", "checks=min(99, old[\"checks\"] + 1)", "checks=old[\"checks\"]"),
    (D, "a settled observation is called observed", "\"observed\" if record[\"checks\"] < MAX_STALE_CHECKS else \"settled\"", "\"observed\""),
    # the downloader
    (U, "a stale copy is saved under the requested day", "            if verdict == \"match\":", "            if verdict in (\"match\", \"stale_copy\"):"),
    (U, "every response is saved", "            if verdict == \"match\":", "            if True:"),
    (U, "the dates are judged against the wrong day", "nd.classify(nd.dates_of_text(text), d)", "nd.classify(nd.dates_of_text(text), d - dt.timedelta(days=1))"),
    (U, "a recent stale copy is recorded", "                if nd.old_enough(iso, today):", "                if True:"),
    (U, "a stale copy is never recorded", "rec = nd.observe(seen, iso, served, today)", "rec = {\"checks\": 1}"),
    (U, "a stale copy is also made a known holiday", "rec = nd.observe(seen, iso, served, today)", "rec = nd.observe(seen, iso, served, today); known_holidays.add(iso)"),
    (U, "a recorded observation suppresses the day forever", "        if iso in seen and not nd.due_for_recheck(seen[iso], today):", "        if iso in seen:"),
    (U, "a recorded observation is asked for every run", "        if iso in seen and not nd.due_for_recheck(seen[iso], today):", "        if False:"),
    (U, "the observations are never written", "    if changed:\n        nd.write_observations", "    if False:\n        nd.write_observations"),
    (U, "the observation is kept when the real file arrives", "                    del seen[iso]", "                    pass"),
    (U, "invalid lines are never rewritten away", "    changed = bool(problems)", "    changed = False"),
    (U, "the future check is not applied", "nd.read_observations(STALE_COPIES, today)", "nd.read_observations(STALE_COPIES)"),
    (U, "a non-UTF-8 file is saved as it arrived", "f.write_bytes(text.encode(\"utf-8\"))", "f.write_bytes(r.content)"),
    (U, "the SYMBOL check is done on the raw bytes", "ok = text is not None and \"SYMBOL\" in text[:300]", "ok = r.status_code == 200 and b\"SYMBOL\" in r.content[:300]"),
    (U, "an undecodable response is taken for a holiday", "        if undecodable:", "        if False:"),
    (U, "the 404 holiday rule changes", "elif (today - d).days > 3:", "elif (today - d).days > 0:"),
    # the market-data report
    (M_, "an observation removes the weekday from the gaps", "    gaps = calendar_gaps(list(days), holidays)", "    gaps = calendar_gaps(list(days), set(holidays) | {r[\"date\"] for r in reports if r.get(\"stale_copy_of\")})"),
    (M_, "all gaps are hidden", "    gaps = calendar_gaps(list(days), holidays)", "    gaps = []"),
    (M_, "a day with a valid file can still carry an observation", "        elif iso in days:", "        elif False:"),
    (M_, "a confirmed holiday is still listed as an observation", "        if iso in holidays:\n            ignored", "        if False:\n            ignored"),
    (M_, "a record outside the data is listed", "        elif iso not in gapset:", "        elif False:"),
    (M_, "an observation is called confirmed", "\"unconfirmed\" if nd.old_enough(iso, today)", "\"confirmed\" if nd.old_enough(iso, today)"),
    (M_, "a recent observation is not marked too recent", "\"unconfirmed\" if nd.old_enough(iso, today) else \"unconfirmed_too_recent_to_judge\"", "\"unconfirmed\""),
    (M_, "the served-day evidence is always true", "\"served_day_file_loaded\": o[\"served_day\"] in days", "\"served_day_file_loaded\": True"),
    (M_, "the row-count evidence is always true", "(rows == counts[o[\"served_day\"]][\"raw_rows\"])", "True"),
    (M_, "the recorded source is lost", "o[\"sources\"].append(\"recorded_response\")", "pass"),
    (M_, "the recorded observations are ignored", "stale_ledger=ledger,", "stale_ledger=None,"),
    (M_, "invalid record lines are not reported", "ledger_problems=len(ledger_problems)", "ledger_problems=0"),
    (M_, "the observation warning is dropped", "    if observed:\n        warnings.append(", "    if False:\n        warnings.append("),
    (M_, "the stale-copy mark is lost", "stale_copy_of=served if verdict == \"stale_copy\" else None", "stale_copy_of=None"),
    (M_, "the genuine-gap count is wrong", "\"weekday_gaps_without_observation\": len(gaps) - len(observed)", "\"weekday_gaps_without_observation\": len(gaps)"),
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
