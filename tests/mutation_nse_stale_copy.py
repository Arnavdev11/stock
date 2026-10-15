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
    (D, "a later date counts as a stale copy", "len(days) == 1 and next(iter(days)) < req and stale_problem(req, next(iter(days))) is None", "len(days) == 1"),
    (D, "classify ignores the believable-served-day rule", " and stale_problem(req, next(iter(days))) is None:", ":"),
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
    # ONE policy for a believable stale answer
    (D, "a weekend requested day is accepted", "    if req.weekday() >= 5:\n        return \"the requested day is a weekend\"", "    if False:\n        return \"the requested day is a weekend\""),
    (D, "a weekend served day is accepted", "    if srv.weekday() >= 5:", "    if False:"),
    (D, "a served day that is not earlier is accepted", "    if not srv < req:", "    if False:"),
    (D, "an implausible gap is accepted", "    if (req - srv).days > MAX_GAP_DAYS:", "    if False:"),
    (D, "the gap boundary moves", "    if (req - srv).days > MAX_GAP_DAYS:", "    if (req - srv).days >= MAX_GAP_DAYS:"),
    (D, "the recorder does not apply the policy", "    why = stale_problem(requested_iso, served_iso)\n    if why:\n        raise ValueError(why)\n", "    pass\n"),
    (D, "the reader does not apply the policy", "why = stale_problem(req, srv) or history_problem(", "why = history_problem("),
    # the persisted observations
    (D, "a record with the wrong number of fields is parsed", "        if len(parts) != 5:\n            problems.append((n, \"expected 5 fields: requested served", "        if False:\n            problems.append((n, \"expected 5 fields: requested served"),
    (D, "dates need not be strict YYYY-MM-DD", "    if not _ISO.fullmatch(text):", "    if False:"),
    (D, "the check count may be any digits", "_COUNT = re.compile(r\"[1-9][0-9]?\")", "_COUNT = re.compile(r\"\\d+\")"),
    (D, "the check count syntax is not enforced", "    if not _COUNT.fullmatch(text):", "    if False:"),
    (D, "the check count is not bounded", "    if n > cap:", "    if False:"),
    (D, "first_seen after last_checked is accepted", "    if first > last:", "    if False:"),
    (D, "one check with two different dates is accepted", "    if count == 1 and span != 0:", "    if False:"),
    (D, "the elapsed time a count needs is not checked", "    if span < (count - 1) * step_days:", "    if False:"),
    (D, "the elapsed-time boundary moves", "    if span < (count - 1) * step_days:", "    if span <= (count - 1) * step_days:"),
    (D, "the elapsed time needed does not grow with the count", "    if span < (count - 1) * step_days:", "    if span < step_days * (count > 1):"),
    (D, "a record first seen too soon is accepted (observations)", "why = stale_problem(req, srv) or history_problem(checks, first, last, REVALIDATE_AFTER_DAYS)\n        if not why and (first - req).days <= RECENT_DAYS:", "why = stale_problem(req, srv) or history_problem(checks, first, last, REVALIDATE_AFTER_DAYS)\n        if False:"),
    (D, "a record first seen too soon is accepted (failures)", "history_problem(attempts, first, last, REQUEST_RETRY_DAYS, \"attempt\")\n        if not why and (first - req).days <= RECENT_DAYS:", "history_problem(attempts, first, last, REQUEST_RETRY_DAYS, \"attempt\")\n        if False:"),
    (D, "a future requested day is accepted", "    if req > today:", "    if False:"),
    (D, "a day outside the supported history is accepted", "    if (today - req).days > HISTORY_DAYS:", "    if False:"),
    (D, "the history boundary moves", "    if (today - req).days > HISTORY_DAYS:", "    if (today - req).days >= HISTORY_DAYS:"),
    (D, "a future last_checked is accepted", "and last > today:\n            why = \"last_checked is in the future\"", "and False:\n            why = \"last_checked is in the future\""),
    (D, "a future last_attempt is accepted", "and last > today:\n            why = \"last_attempt is in the future\"", "and False:\n            why = \"last_attempt is in the future\""),
    (D, "a duplicate day is accepted (observations)", "        if not why and req.isoformat() in records:\n            why = \"the requested day is listed twice\"\n        if why:\n            problems.append((n, why))\n            continue\n        records[req.isoformat()] = {\"requested\": req.isoformat(), \"served\"", "        if False:\n            why = \"the requested day is listed twice\"\n        if why:\n            problems.append((n, why))\n            continue\n        records[req.isoformat()] = {\"requested\": req.isoformat(), \"served\""),
    (D, "problems are dropped silently", "            problems.append((n, why))\n            continue", "            continue"),
    (D, "an unreadable file is silently empty", "        return [], [(0, \"the file is not readable text; none of it was used\")]", "        return [], []"),
    (D, "an observation is re-asked forever", "record[\"checks\"] < MAX_STALE_CHECKS and", "True and"),
    (D, "the re-check boundary moves", ">= REVALIDATE_AFTER_DAYS", "> REVALIDATE_AFTER_DAYS"),
    (D, "an observation is always due", ">= REVALIDATE_AFTER_DAYS", ">= 0"),
    (D, "a different answer does not start again", "    if old and old[\"served\"] == served_iso:", "    if old:"),
    (D, "a repeated answer adds no check", "checks=min(MAX_STALE_CHECKS, old[\"checks\"] + 1)", "checks=old[\"checks\"]"),
    (D, "a settled observation is called observed", "\"observed\" if record[\"checks\"] < MAX_STALE_CHECKS else \"settled\"", "\"observed\""),
    # what one HTTP answer is
    (D, "a 403 is not an access problem", "    if status in (401, 403):", "    if False:"),
    (D, "a 429 is not a rate limit", "    if status == 429:", "    if False:"),
    (D, "a 5xx is not a server error", "    if 500 <= status <= 599:", "    if False:"),
    (D, "any other status is treated as data", "    if status != 200:", "    if False:"),
    (D, "a 404 is not 'not found'", "    if status == 404:", "    if False:"),
    (D, "an HTML or error page is accepted as data", "    if not has_symbol_header(text):", "    if False:"),
    (D, "the header check only looks for the word", "return \"SYMBOL\" in [c.strip() for c in line.split(\",\")]", "return \"SYMBOL\" in line"),
    (D, "undecodable bytes are accepted", "    if text is None:\n        return \"undecodable\", None, None", "    if False:\n        return \"undecodable\", None, None"),
    # the persisted failed requests
    (D, "an unknown failure kind is accepted", "        if kind not in FAILURE_KINDS:\n            why = \"unknown kind", "        if False:\n            why = \"unknown kind"),
    (D, "rate limits and server errors get no extra attempts", "return MAX_ATTEMPTS_TRANSIENT if kind in TRANSIENT_KINDS else MAX_ATTEMPTS_OTHER", "return MAX_ATTEMPTS_OTHER"),
    (D, "a failed request is retried forever", "    return record[\"attempts\"] < max_attempts(record[\"kind\"]) and", "    return True and"),
    (D, "a failed request may be repeated within the day", ">= REQUEST_RETRY_DAYS", ">= 0"),
    (D, "a different failure does not start again", "    if old and old[\"kind\"] == kind:", "    if old:"),
    (D, "a repeated failure adds no attempt", "attempts=min(max_attempts(kind), old[\"attempts\"] + 1)", "attempts=old[\"attempts\"]"),
    (D, "a given-up request is called retrying", "\"retrying\" if record[\"attempts\"] < max_attempts(record[\"kind\"]) else \"given_up\"", "\"retrying\""),
    # the downloader
    (U, "a stale copy is saved under the requested day", "            if verdict == \"match\":", "            if verdict in (\"match\", \"stale_copy\"):"),
    (U, "every response is saved", "            if verdict == \"match\":", "            if True:"),
    (U, "the dates are judged against the wrong day", "nd.classify(nd.dates_of_text(text), d)", "nd.classify(nd.dates_of_text(text), d - dt.timedelta(days=1))"),
    (U, "a recent stale copy is recorded", "                if old:\n                    rec = nd.observe(seen, iso, served, today)", "                if True:\n                    rec = nd.observe(seen, iso, served, today)"),
    (U, "a stale copy is never recorded", "rec = nd.observe(seen, iso, served, today)", "rec = {\"checks\": 1}"),
    (U, "a stale copy is also made a known holiday", "rec = nd.observe(seen, iso, served, today)", "rec = nd.observe(seen, iso, served, today); known_holidays.add(iso)"),
    (U, "a recorded observation suppresses the day forever", "        if iso in seen and not nd.due_for_recheck(seen[iso], today):", "        if iso in seen:"),
    (U, "a recorded observation is asked for every run", "        if iso in seen and not nd.due_for_recheck(seen[iso], today):", "        if False:"),
    (U, "the observations are never written", "    if changed:\n        nd.write_observations", "    if False:\n        nd.write_observations"),
    (U, "the observation is kept when the real file arrives", "                    del seen[iso]", "                    pass"),
    (U, "invalid lines are never rewritten away", "    changed = bool(problems)", "    changed = False"),
    (U, "the future check is not applied", "nd.read_observations(STALE_COPIES, today)", "nd.read_observations(STALE_COPIES)"),
    (U, "a non-UTF-8 file is saved as it arrived", "f.write_bytes(text.encode(\"utf-8\"))", "f.write_bytes(r.content)"),
    (U, "a recent 404 becomes a holiday", "            if old:                   # older than 3 days", "            if True:                  # older than 3 days"),
    (U, "any failure is taken for a holiday", "        if kind == \"not_found\":", "        if kind != \"data\":"),
    (U, "a failed request is never recorded", "rec = nd.record_failure(failed, iso, kind, today)", "rec = {\"attempts\": 1, \"kind\": kind, \"last_attempt\": iso, \"first_seen\": iso}"),
    (U, "a recent failed request is recorded", "            if old:\n                rec = nd.record_failure", "            if True:\n                rec = nd.record_failure"),
    (U, "a failed request suppresses the day forever", "        if iso in failed and not nd.request_due(failed[iso], today):", "        if iso in failed:"),
    (U, "a failed request is asked for every run", "        if iso in failed and not nd.request_due(failed[iso], today):", "        if False:"),
    (U, "the failed requests are never written", "    if fchanged:\n        nd.write_failures", "    if False:\n        nd.write_failures"),
    (U, "the failure record is kept when the real file arrives", "                    del failed[iso]\n                    fchanged = True\n                    log(\"the file for\"", "                    pass\n                    fchanged = True\n                    log(\"the file for\""),
    (U, "the failure record is kept after a 404 holiday", "                known_holidays.add(iso)\n                if iso in failed:\n                    del failed[iso]", "                known_holidays.add(iso)\n                if iso in failed:\n                    pass"),
    (U, "invalid failure lines are never rewritten away", "    fchanged = bool(fproblems)", "    fchanged = False"),
    (U, "the failure ledger future check is not applied", "nd.read_failures(FAILED_REQUESTS, today)", "nd.read_failures(FAILED_REQUESTS)"),
    (U, "the failure ledger is not the one in data/", "FAILED_REQUESTS = ROOT / \"data\" / nd.FAILURE_FILE_NAME", "FAILED_REQUESTS = ROOT / \"data\" / nd.STALE_FILE_NAME"),
    # the market-data report
    (M_, "an observation removes the weekday from the gaps", "    gaps = calendar_gaps(list(days), holidays)", "    gaps = calendar_gaps(list(days), set(holidays) | {r[\"date\"] for r in reports if r.get(\"stale_copy_of\")})"),
    (M_, "a failed request removes the weekday from the gaps", "    gaps = calendar_gaps(list(days), holidays)", "    gaps = calendar_gaps(list(days), set(holidays) | set(request_ledger or {}))"),
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
    (M_, "the failed requests are ignored by the report", "request_ledger=failures,", "request_ledger=None,"),
    (M_, "invalid failure lines are not reported", "request_ledger_problems=len(failure_problems)", "request_ledger_problems=0"),
    (M_, "the failed-request warning is dropped", "    if failed:\n        kinds = {}", "    if False:\n        kinds = {}"),
    (M_, "a failure record for a day with a file is listed", "        elif iso in days:\n            ignored[\"has_a_valid_file\"] += 1\n        elif iso not in gapset:\n            ignored[\"outside_loaded_range\"] += 1\n        else:\n            out.append({\"date\": iso, \"kind\"", "        elif False:\n            ignored[\"has_a_valid_file\"] += 1\n        elif iso not in gapset:\n            ignored[\"outside_loaded_range\"] += 1\n        else:\n            out.append({\"date\": iso, \"kind\""),
    (M_, "a failure record for a known holiday is listed", "    for iso, rec in sorted(ledger.items()):\n        if iso in holidays:", "    for iso, rec in sorted(ledger.items()):\n        if False:"),
    (M_, "a given-up request is called retrying in the report", "\"status\": nd.request_state(rec)", "\"status\": \"retrying\""),
    (M_, "the failed-request count is wrong", "\"weekday_gaps_with_failed_nse_response\": len(failed)", "\"weekday_gaps_with_failed_nse_response\": 0"),
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
