# Backfill planner (read-only)

`backfill_plan.py` answers "what is left to fetch, in which batches of up to 500, and how many provider calls and runs will it take?" It **fetches nothing**:
no network, no provider, no secret, no workflow dispatch, and it never writes into the data branch or state directory.

```
python3 backfill_plan.py --data-branch DIR --as-of YYYY-MM-DD --out OUTDIR \
    [--state-dir DIR] [--batch-size 500] [--datasets a,b] [--priority-file FILE] [--previous OUTDIR/backfill_plan.json] [--dry-run]
```

Output: `backfill_plan.json` (machine-readable) and `backfill_plan.md` (human-readable), written atomically into `OUTDIR` only. `--dry-run` prints and writes nothing.

## Design
* **No second downloader, no second ledger.** Fetching stays in the existing updaters and workflows (`historical.yml`, `financial_history.yml`, `fundamentals_batch.yml`, the daily `update.yml`),
  each with its own pacing, retries, timeouts, call budgets and concurrency group. The planner only measures.
* **Progress is derived from the data, never from a marker.** Counts come from `coverage_report.py`'s own module functions, so a stock is complete only if its records pass the same
  checks. There is no progress manifest that could drift from the data. `--previous` reconciles an earlier plan and lists any batch/dataset it claimed complete that the data no longer supports.
* **Unmeasured is not zero and not complete.** Fundamentals, quarterly results and annual financials live in the GitHub Actions cache; they are measured only with `--state-dir`.
  Without it they are reported as "not measurable" and block any completion claim.
* **"All complete" requires every requested dataset to be measurable and fully valid** (no stale, missing, invalid or unverified stocks). Shareholding and sector are not planned.
* **Order** is the universe file's order. That file is alphabetical, so the "first 500" are alphabetical, not the largest companies; the report says so. `--priority-file` (one symbol per line) puts named symbols first.
* **Cost model** (`DATASETS`) is pinned by tests to the updater/workflow constants it comes from. Calls are nominal first attempts; `calls_worst_case` multiplies by the retry count.

## Where durable progress lives
Historical candles and financial history: the data branch (shards, ledger, attempts file). Fundamentals, quarterly, annual financials: the Actions cache (immutable, saved on a key miss, eviction possible), so it is not a permanent store.
