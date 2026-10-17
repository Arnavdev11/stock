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
| 2. A cache was restored but holds no state | the lookup resolved a key (for example `nse-data-shp-…` of another workflow) | stop; names the key; a maintainer decides |
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
* Not exercised against real GitHub Actions: the lookup step's `cache-matched-key` with `lookup-only` and restore-keys, the `total_count` of the cache list, and the
  Pages download are only simulated with stubs. A rehearsal is needed before relying on the recovery.
