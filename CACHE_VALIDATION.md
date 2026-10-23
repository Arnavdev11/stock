# Validating `coverage_report.py --state-dir` against the real saved state

**Status: this workflow has not been run. Adding it validates nothing.** Only a successful run, and a person reading its job summary, would.

**What the result means: cache coverage, not publication status.** It counts what the saved updater caches hold. A cached record is not proof that anything reached the
website, and a stock the cache lacks may still be published from another source. Do not read the numbers as what the site shows.

## What the workflow does

`.github/workflows/cache_validation.yml` ("Validate cache coverage") is manual only (`workflow_dispatch`).

1. It lists the repository's cache **metadata** (no content) for the default branch and picks the newest cache whose key matches `^nse-data-[0-9]+$` exactly. That is the
   key of the daily "Update data and publish site" run and of a fundamentals batch. The caches other workflows save under the same prefix (`nse-data-shp-...`,
   `nse-data-sector-...`) never match. If no cache fits, or the list is incomplete, the run fails.
2. It restores **that key only**: no `restore-keys`, `fail-on-cache-miss`, and the same paths as the daily run (`data` and `out`). The cache version is derived from the
   paths, so a different path set would silently miss. Nothing is saved.
3. It fails unless `fundamentals_cache.json`, `financials_cache.json` and `quarterly_cache.json` are all there as non-empty files.
4. It runs `coverage_report.py --state-dir data` offline against the universe on the `stocklens-data` branch. The report stays on the runner.
5. It writes the **job summary only**, built new from an allowlist (`cache_validation.py summarize`).

**It does not** save a cache, upload an artifact, deploy, touch GitHub Pages, write to a branch, call a provider or use a secret. The only token is the workflow's own
read-only one, used to list cache metadata. **No artifact is uploaded in this first version.**

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
* **A cache must exist.** Caches are removed after 7 days without access or at the 10 GB repository limit, and none has been confirmed. If none matches, the run fails
  with a clear message and changes nothing.
* **The selected cache may not be the one intended.** A fundamentals batch also saves `nse-data-<run id>`, so the newest exact match can be a batch's cache. The summary
  records the key, creation time and size; a person decides whether that is the cache they meant.
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
