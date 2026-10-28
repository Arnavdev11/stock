# Fundamentals: recovery after a cache miss

The scheduled "Update data and publish site" run restores the saved data (`data/` and `out/`, key `nse-data-<run id>`, restore prefix `nse-data-`).
`data/fundamentals_cache.json` is the private fundamentals state; without it the run would publish only the few stocks one run can fetch.

## What the workflow decides (update.yml, right after the restore, before any updater and before the deploy job)

`actions/cache@v4` (the combined action) has only a `cache-hit` output, which is empty both for "nothing restored" and for some failures, so it cannot tell the
cases apart. `cache-matched-key` belongs to `actions/cache/restore`. A read-only second step (`restore` with `lookup-only: true`, same path, key and restore-keys,
nothing downloaded or written) reports the cache the restore resolves to.

| Condition | How it is recognised | Result |
|---|---|---|
| 1. A state was restored | `data/fundamentals_cache.json` exists | `check-state`: usable, or stop. Never touched, never recovered over. |
| 2. A cache was restored but holds no state | the lookup resolved a key (for example `nse-data-shp-…` of another workflow; since PR #33 the shareholding workflow saves such a cache only when its job succeeded and `data/fundamentals_cache.json` exists, which does not prove the state is valid, and older such caches may still exist) | stop; names the key; a maintainer decides |
| 3. No cache at all | lookup resolved nothing **and** the complete cache list of this ref and the default branch holds no `nse-data-` key (all caches are read and filtered with `startswith` in `jq`; the API's `key` filter is not used) | recover from the published site (below) |
| 4. Cache store unreadable | lookup step did not succeed, a list call failed, a list is incomplete (fewer caches read than `total_count`) or its total changes while paging | stop; re-run when the service answers; never a miss |
| 5. A cache exists but was not restored | the lookup resolved a daily key without a state, or nothing resolved but caches are listed | stop; names the condition; a re-run is advised only where it can help |

Every stop leaves the site as it is (the deploy job needs the build job) and nothing is deleted or changed: the workflow only lists caches (`actions: read`) and never
deletes one. Removing a cache or approving a manual recovery is a maintainer decision.

## What is checked before anything is recovered (`fundamentals_updater.py seed`)

The published `out/fundamentals.json` is validated as before (strict JSON, source Upstox, ISINs, dates, finite metrics, no stock outside the universe, coverage
numbers consistent) and now also: it must carry its `coverage` block (the file cannot otherwise show that it is complete); `as_of` must equal the newest row date
(how the writer sets it); and `as_of` must be newer than `RATIOS_MAX_AGE_DAYS` (7, the project's own rule for stale ratios). Nothing is written on any failure; the
state is written before the output.

## What is checked on a restored state (`check-state`)

Every row of the restored `out/fundamentals.json` must still be buildable from the restored state (the same `build_rows` rule that publishes it). A state that lost
records its own output lists would silently drop those stocks, so it stops. This is an exact rule, not a percentage: records that are not in the output (failed,
not listed, held) are normal and are not counted.

## Not recoverable from the published file

Profile dates (the sector refreshes with the next ratio refresh), failure counts and retry dates (failed stocks are retried at once), batch records, the batch
gate and the validation history, publication holds (the published values already are the held ones), records held aside after an ISIN change, the call log, the
dates since which stocks are not listed, the ISIN the instrument list gave at the last run, and metrics the provider marked as having no value. A recovered state is
a valid start, not a copy of the lost one.

## Known limits (not decided here)

* A published file that is itself reduced but self-consistent cannot be told from a small one in the middle of a legitimate fill: there is no baseline in the repository
  and no defensible percentage, so it is recovered as it is (the site already shows it).
* A published file older than 7 days, a changed universe size (the existing `coverage.universe` check), or a stock that left the universe stop the recovery:
  a maintainer decides. After an outage of more than 7 days the caches may also have expired (GitHub removes caches unused for 7 days), so this can coincide.
* Not yet observed in production: see "Verification status" below. The rehearsal used synthetic `rt31-` caches on a scratch branch, not the real `nse-data-` caches.

## Verification status

### Verified in a GitHub Actions rehearsal

Scratch branch `rehearsal/pr31-cache-recovery`, run 39042332308 on commit `237ebcd`; all 12 jobs concluded `success`. The production step scripts were extracted from
`update.yml` and run against synthetic `rt31-` caches. Replaced in them: the `nse-data-` prefix; in p1 and p2 the page size (`per_page=100` to `per_page=1`); and in p2
also `--paginate` was removed, to deliberately test a truncated cache listing. No provider was called and nothing was deployed or published (by the workflow's design and
its guard job; the job logs were not inspected).

Supported by readable evidence (check-run annotations and step conclusions):

* s2: a newer cache without state (like `nse-data-shp-…`) in front of an older one with state stops the run. The annotation names the resolved shp key and says it
  "shadows the daily state cache".
* s3: no cache at all is read as a genuine miss (annotation: "a genuine cache miss … recovered from the published site"); the recovery step concluded `success`.
  According to the rehearsal workflow's assertions, recovery included seed validation of the published file available that day; the step logs were not inspected.
* s4: a cache saved for another path set is listed but not resolved by the restore, and the run stops (annotation: "the restore resolved none of them").
* s1: the check step concluded `success` and the recovery step was `skipped` (a state-bearing cache was restored). The log lines themselves were not read.
* The lookup step's `cache-matched-key` with `lookup-only` and restore-keys behaved as the workflow assumes, as far as s2 (a key resolved) and s4 (none resolved) show.

Supported only by the rehearsal workflow's own pass/fail assertions (the jobs concluded `success`; their logs and summaries were not available for inspection):

* s5: a cache list refused with HTTP 401 stops the run and is never read as a miss.
* pg: a failed Pages download stops the run and writes nothing.
* p1 and p2: with paging forced by `per_page=1`, a complete list is read in full (p1) and a truncated one is reported as incomplete (p2); neither is read as a miss.
* The `total_count` behaviour of the cache list, which p1 and p2 depend on.

### Implemented in the workflow, not exercised in any real run

The ISIN hold-aside check and the `Require the saved fundamentals state` step of `fundamentals_batch.yml`. The rehearsal's `check-state` ran only on a one-stock fixture.

### Awaiting production verification

A qualifying scheduled run on `main` is needed to verify: the restore, lookup and `check-state` steps against the real `nse-data-` caches of `main`; whether the
real restored state passes `check-state`; the recovery on the real published file; the shareholding workflow's guarded save (`hashFiles`) in a real run (its
rehearsal, run 39050410705, used synthetic caches); and the data published afterwards. Nothing in this document is a claim that the recovery has worked in production.
