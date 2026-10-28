# Cache-only datasets: measuring them and making their progress durable (PROPOSAL - nothing here is built or enabled)

Fundamentals, quarterly results and annual financial statements keep their only state in the GitHub Actions cache (`data/fundamentals_cache.json`, `quarterly_cache.json`, `financials_cache.json`).
Automatic continuation stays **off** for them until (A) their coverage can be measured and (C) a lost cache cannot silently reset them. Three different needs, three different mechanisms:

## A. A read-only coverage snapshot (measurable)
`cache_validation.yml` (already merged, manual) already restores the newest cache saved by a successful daily run, checks its provenance marker and runs `coverage_report.py --state-dir data` against it.
Today it publishes only an allowlisted job summary. **Proposal:** add the planner's per-dataset counts (valid / stale / missing / invalid, calls still needed) to that same allowlisted summary, so the
three datasets become *measured* without a new workflow, a new secret or any provider call. Limits: it needs a recent daily run (the cache must be at most 7 days old and have a valid marker); it is a
point-in-time reading; a job summary is not retrievable by this tooling through the REST API, so a person reads it in the Actions tab.

## B. A persistent record of the latest known state (a record, not a ledger)
An Actions artifact is not permanent: it expires after its `retention-days` (14 in this repository's workflows) and can be deleted. A cache is not permanent either: GitHub keeps 10 GB per repository by default,
evicts the least recently used entries when that is exceeded and drops entries not accessed for seven days ([GitHub changelog](https://github.blog/changelog/2025-11-20-github-actions-cache-size-can-now-exceed-10-gb-per-repository/)).
**Proposal:** after a measured snapshot, a manual, separately approved job writes ONE small derived file to the data branch, for example `coverage_state/<dataset>.json`: per stock only the last-fetched date
and a status code (no financial values), plus the source run id and the cache provenance hash. It is labelled *derived, not authoritative*, is rewritten whole each time, and is never read by an
updater, so it cannot compete with the caches as a second ledger. It answers "what did we last know?" if the cache is gone; it does not hold the data.

## C. A safe mechanism to resume provider fetching
Resuming means the updater knows what it already fetched. Today that knowledge is the cache itself (stalest-first, budgeted, re-check windows for quarterly results), which is safe while the cache restores
and costs only repeated calls if it is evicted - it does not corrupt data. Making the *data* durable (so an eviction loses nothing) needs a decision that is not mine to make here: move each dataset's
authoritative store to a data-branch file saved through the same checked hand-over the historical and financial-history ledgers use (`save_ledger.yml` / `save_historical.yml`: no-loss check, one writer).
That changes the updaters and the daily workflow, so it needs its own design, tests and approval, one dataset at a time (fundamentals first: it has the batch workflow and the largest backlog).

## Order of work, and what stays off
1. A (extend the allowlisted summary) - read-only, smallest step. 2. B (derived record) - only after A has been read successfully. 3. C (durable authoritative stores) - per dataset, separately approved.
Until a dataset has A and C, `backfill_controller.py` refuses it (`not_automatic`) and no scheduler may include it. Shareholding stays a fixed 15-stock collector; widening it is a separate approval.
