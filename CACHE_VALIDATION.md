# Validating `coverage_report.py --state-dir` against the real saved state

**Status: this workflow has not been run. Adding it validates nothing.** Only a successful run, and a person reading its job summary, would.

**What the result means: cache coverage, not publication status.** It counts what the saved updater caches hold. A cached record is not proof that anything reached the
website, and a stock the cache lacks may still be published from another source. Do not read the numbers as what the site shows.

## Which cache is validated, and why that is not trivial

The aim is the saved state of the **daily** workflow, "Update data and publish site" (`update.yml`). Two workflows save a cache under a key of the form `nse-data-<run id>`:
`update.yml` and `fundamentals_batch.yml` (a batch saves its progress between daily runs). The newest `nse-data-<digits>` cache can therefore be a batch's intermediate state.

**What GitHub exposes.** The cache list returns only `id`, `ref`, `key`, `version`, `last_accessed_at`, `created_at` and `size_in_bytes`. **It does not record which
workflow, run or job created a cache.** A workflow run does expose its `id`, `workflow_id`, `path`, `head_branch`, `event`, `status`, `conclusion`, `created_at`, `updated_at`
and `run_attempt`. So the owner of a cache cannot be read; it can only be **corroborated**.

**The rule (every part must hold; anything else fails the run with a fixed code).**

1. The daily workflow's own metadata is read and must say: path `.github/workflows/update.yml`, name "Update data and publish site", state active; its id is noted.
2. The cache list (of the default branch, complete: entries read equal the reported total) gives the caches of that branch whose key matches `^nse-data-[0-9]+$` exactly. A key may
   appear once on the branch. The newest ten, by creation time, are examined newest first.
3. For each, the run whose id is in the key is fetched. Its `id` must equal the key's number. Then:
   * if the run's workflow path is `fundamentals_batch.yml`, that cache is **skipped** (counted in the summary). This is the only skip;
   * if the run's path is anything else than `update.yml`, the run fails;
   * for a daily run, its `workflow_id` must equal the daily workflow's id, it must be `completed` with conclusion `success`, on the default branch, started by `schedule` or
     `workflow_dispatch`, and the cache must have been **created during the run** (from the run's creation to its last update plus 5 minutes) and be **at most 7 days old**
     and not from the future.
4. The run metadata being missing, unreadable or of another shape stops the run. It does **not** fall back to an older cache. A failed or cancelled daily run stops the run too.
5. Only the selected key is restored: no `restore-keys`, `fail-on-cache-miss`, with the daily run's paths (`data`, `out`). The restore must resolve to exactly that key.

The **cache version** (a hash derived from the paths and compression) is not recomputed here. Restoring with the daily run's path set and `fail-on-cache-miss` is the check:
a cache with a different version cannot be restored under that set. The list's `version` is recorded in the summary.

## The provenance marker (Tier 1)

The rule above only corroborates. To bind a cache to its run by **content**, the daily workflow (`update.yml`, merged to `main` in PR #39) removes any restored
marker before it updates data and writes `data/cache_provenance.json` as its last step (atomically, in `data/`, never in `out/`, never published). It records the workflow,
repository, ref, commit, run id, **run attempt**, cache key, UTC time, the data status, the outcome of each data step, and the byte size and SHA-256 of the three state files.

After restoring the exact cache this workflow **refuses unless** the marker is present and agrees with the selected run on every one of those: repository, workflow, workflow ref,
branch, run id, cache key, event, commit, and the run's **latest attempt**; was written inside the run and just before the cache was saved; says the data update was usable
(`data_ok` true, status `complete` or `incomplete`, and the fundamentals, financials and quarterly steps all succeeded); and matches the size and SHA-256 of all three restored
state files. An `incomplete` status is shown in the summary as **not a full data refresh**. The marker is read as untrusted input: capped at 16 KiB, no symlink, no duplicate keys,
exact fields and types, fixed refusal codes only.

**Re-runs (verified against how Actions caches behave).** A cache is immutable and a new one is saved only on a miss. A re-run of the same run id hits its own key, restores it
and saves nothing, so the cache keeps the **first attempt's** marker. The marker's attempt must equal the run's latest attempt, so a cache left by an earlier attempt is refused
rather than trusted; an attempt that is the first to succeed (an earlier attempt having failed and saved nothing) is accepted. A marker from an earlier run can never match a newer
cache, because the run id, cache key, commit, attempt and file hashes must all agree.

**Limits.** The marker is **not signed (Tier 1)**. It defends against a mix-up between workflows, runs and attempts, and against a modified or truncated state file. It does not
defend against someone who can already edit a workflow or write the cache; **Tier 2** (an attestation) is not part of this change. **No existing cache has a marker.** The first
cache this workflow can accept appears after the first **scheduled daily run** that follows the merge of PR #39 (a run that started before it saved a cache without one); until then it refuses with
`marker_missing`. Do not dispatch the daily workflow for this: it spends provider quota.

## What the workflow does

`.github/workflows/cache_validation.yml` ("Validate cache coverage") is manual only (`workflow_dispatch`).

1. Reads the daily workflow's metadata, the cache metadata (no content) and the metadata of the newest candidate runs (read-only `GET` requests with the workflow's own token).
2. Selects the cache by the rule above.
3. Restores that key only. Nothing is saved.
4. Fails unless `fundamentals_cache.json`, `financials_cache.json` and `quarterly_cache.json` are all present, non-empty regular files, and unless the provenance marker
   verifies (see above).
5. Runs `coverage_report.py --state-dir data` offline against the universe on the `stocklens-data` branch. The report stays on the runner.
6. Writes the **job summary only**, built new from an allowlist (`cache_validation.py summarize`).

**It does not** save a cache, upload an artifact, deploy, touch GitHub Pages, write to a branch, call a provider or use a secret. **No artifact is uploaded in this first version.**

## Limits of the selection (read these)

* **The link is corroborated, not proven.** GitHub does not record which run created a cache. This rule relies on the repository's own convention (the run id is in the key) and
  on the checks above (the run exists, is the exact daily workflow, succeeded, was on the default branch, and contained the cache's creation). That makes a mix-up very unlikely
  in this repository, where the daily and batch workflows share one concurrency group and so never overlap in time. It is not a guarantee from GitHub. A workflow that
  deliberately named a cache with another run's id would defeat it. The summary says "corroborated, not proven".
* **Batch identification relies on the run's workflow path.** Only `fundamentals_batch.yml` is recognised for skipping. If another workflow starts saving `nse-data-<run id>`,
  a test fails and this rule must be extended; until then such a cache stops the run.
* **Newer batch state is not seen.** The validated cache is the daily workflow's, which may be older than a batch's progress since then.
* **Seven days** is the eviction horizon for an unused cache; a cache older than that, or one the daily workflow has not refreshed, stops the run.
* If a cache is saved by a daily run that later failed (for example in the deploy job), the run's conclusion is `failure` and the run stops rather than using it.

**The marker closes most of this gap** (see the section above) once a scheduled daily run after PR #39 has saved a cache. It is unsigned.

## What the job summary contains and never contains

Contains: the scope sizes; for each of `fundamentals_state`, `quarterly_results` and `annual_financials_and_balance_sheet` its measurability, counts (valid, stale,
missing, invalid, unverified), reason counts, a few counts of its own (for example how many stocks have a failed request, no data or a rejected response); which
state files were usable and why not; the cache key, dates and size; the run, commit and data-branch commit; the byte size and SHA-256 of each state file.

Never contains: a record, ratio, company name, ISIN, sector label, error text, symbol (so no outside-the-universe list), or path.

The summary is **built from an allowlist**, not filtered. Every number must be a plain non-negative integer, the counts must add up to the scope size, and every reason or
warning name must be a plain lower-case token. Anything else, or a report that differs from the reviewed shape, refuses the whole run and prints only a fixed code.

## Before it can be run

* **It must be on the default branch.** GitHub: "To trigger the `workflow_dispatch` event, your workflow must be in the default branch." It cannot be started from this
  pull request's branch; merging is a separate decision.
* **A cache with a valid marker must exist.** That needs one scheduled daily run after PR #39 was merged. Caches are removed after 7 days without access or at the 10 GB repository limit, and none has been confirmed. If none matches, the run fails
  with a clear message and changes nothing.
* **The selected cache is the newest one saved by a successful daily run**, found by the rule above. If a batch saved a newer cache, it is skipped and counted. If the daily
  run's metadata cannot be matched, the run fails and says only a fixed code.
* **A restore refreshes the cache's last-accessed time** (it extends its life). That is the only effect on the cache.
* Running it needs a person to dispatch it, and the stocklens-ledger group means it waits behind any workflow that writes the saved caches.

## Reading the result

* A state file shown as not usable (`invalid_json`, `unsupported_schema`, `unreadable`) makes its module `not_measurable`: every stock is unverified, never zero coverage.
  A file that is simply absent stops the run before the report.
* `unverified` includes records whose ISIN differs from the universe's; they are not counted as covered.
* `valid` for quarterly results means a check within the last 30 days, not that the latest quarter is present.
* Compare the counts with expectations; do not treat them as verified until a person has read the summary of a real run.

## Tests

`test_cache_validation.py` checks cache selection, missing files, the allowlist (with hostile synthetic values), fail-closed behaviour and the workflow's controls. It
uses synthetic data only and validates no real cache. No workflow runs these tests automatically.
