# Bounded, resumable data backfill

This adds a **read-only controller** (`backfill_controller.py`) and a **manual, read-only helper workflow** (`.github/workflows/backfill_next.yml`).
Nothing here fetches data, uses a secret, writes to a branch or cache, publishes, raises a budget, or starts another workflow.
The fetching, pacing, retries, call budgets, timeouts, ledgers and concurrency groups stay exactly where they are: in the existing updaters and workflows.

## The loop

1. **Plan** (`next`): read the data branch, measure real coverage (the same checks as `coverage_report.py`), pick the next cohort of up to 500 stocks in priority order, and print
   the existing workflow and the exact inputs to use, or the reason there is nothing to do.
2. **Run** (a person, today): start that existing workflow with those inputs. It fetches within its own call budget and saves through its own checked hand-over.
3. **Verify** (`verify`): compare the data branch before and after, classify every stock of the cohort, fail if anything saved was lost, report the next cohort or the stopping reason.
4. **Resume**: step 1 again. Progress is *derived from the data* (and the candle updater's own `attempts.json`), never from a marker, so it cannot say "done" about missing data, and a
   stopped or failed run simply leaves the stock still needing work.

A cohort of 500 is a selection of stocks, not 500 requests: each updater still stops at its call budget (candles about 22 stocks per default 150-call run; financial history 60 stocks per 120-call run).

## Datasets

| Dataset | Automatic continuation | Existing workflow | Where progress is saved | Complete means (per stock) | Budget (default / refusal cap) |
|---|---|---|---|---|---|
| Historical candles | yes | `historical.yml` (manual, batch mode) | data branch `historical/` (shards, `historical.json`, `attempts.json`) via `save_historical.yml` | candle history present, undamaged and within `FRESH_DAYS` of the last complete day | 150 / 3,000 (`CALL_CEILING`; the plan keeps 10% headroom) |
| Annual financial history and revisions | yes | `financial_history.yml` (manual) | data branch `financial_history_ledger.json` via `save_ledger.yml`; the updater's no-loss check runs first | year records present and both statements fetched within 30 days | 120 / 1,500 (this tool's own cap, about 50 minutes) |
| Fundamentals | **no** | `fundamentals_batch.yml` by hand | Actions cache only | n/a | n/a |
| Quarterly results | **no** | daily `update.yml` | Actions cache only | n/a | n/a |
| Annual financial statements | **no** | daily `update.yml` | Actions cache only | n/a | n/a |
| Shareholding | **no** | `shareholding.yml` | fixed 15-stock NSE/XBRL collector by design | n/a | n/a |

The Actions cache is immutable and can be evicted, so it is not a durable record of progress. `next` refuses those datasets and says why; it never guesses their coverage.
**The platform is never reported complete while any dataset is unmeasurable.**

Completion for a stock is never "the request returned HTTP success": it is what `coverage_report.py` finds in the saved records (required fields and periods). Missing data is never
counted as zero, no value is invented, and a stock the provider cannot serve stays *missing* (the counts say what the files hold, not what the provider could supply).

## Per-stock outcomes (`verify`)

`verify` checks **every saved stock and the benchmark**, not only the cohort: a lost file, a shortened candle history, a lost year or a value turning null anywhere fails the run
(**verification: failed**, exit 1, the next step is blocked, nothing should be published). Then it classifies each stock of the cohort from what actually changed in the data:

`improved` (new data for a stock that had none, or new years) - `refreshed` (newer candles or a newer fetch) - `retryable_failed` (the candle `attempts.json` recorded a failure, or the
ledger recorded a fetch error; it is retried after the cooldown / later in the order) - `needs_review` (candles: 3 or more recorded failures) - `not_reached` (no evidence the run
processed it: the budget ended first, or the workflow's own plan did not include it) - `regressed`.

It also counts the stocks that changed **outside** the printed cohort, because the workflow re-plans on its own day and can pick a different set. `not_reached` is not a failure.

Financial history revisions follow the repository's own rule (`ledger_storage.no_loss_problems`): a changed value is fine only if the old value is recorded as a revision; a lost year
or a value turning null is a regression. Consolidated and standalone year records are keyed separately by that same check. Candle gaps inside a history are not re-measured by `verify`
(it checks the count and last date); `coverage_report.py` is the tool for period gaps.

## Stop conditions

`nothing_to_do` (every stock up to date or damaged), `waiting_cooldown` (everything left failed recently; the wake-up date is printed), `paused`, `not_automatic`, a refusal (bad input,
unreadable ledger, budget above the cap), or `verification: failed`. There is no recursion: no run starts another.

## The manual cycle (nothing here starts a production run for you)

All of these steps are performed by a person. The helper workflow and the controller only read.

1. **Plan.** Actions -> *Backfill - plan or verify the next run (read-only)* -> mode `next`, pick the dataset (and optionally `max_calls`, `batch_size`, `priority_symbols`). Or locally:
   `python3 backfill_controller.py next --data-branch DATA --dataset historical_candles --as-of YYYY-MM-DD --out OUT --data-sha <40-char SHA of DATA> [--max-calls N] [--priority-symbols A,B,C]`.
   Add `--dry-run` to print without writing. The report ends in the exact existing workflow, its inputs and the cohort.
2. **Record the snapshot.** Write down the line `Data branch checked: <40-character SHA>`. That is the "before" snapshot. If you later find the SHA moved (a scheduled refresh saved something in between), plan again.
3. **Read the plan.** Check the workflow name, the call budget (never above the ceiling: candles 3,000, financial history 1,500 - both are refused above it), the estimated minutes and the cohort.
4. **Start the existing workflow by hand** (`historical.yml` or `financial_history.yml`) with the printed inputs. Do not start two data workflows at once; the concurrency groups protect their
   own files, not your judgment about overlap.
5. **Wait for it to finish and for its save job to complete.** The data only counts once it is on `stocklens-data`.
6. **Verify.** Mode `verify` with `before_ref` = the SHA from step 2, `cohort` = the cohort from step 1 and, optionally, `calls_used` from the run's own status summary. The workflow proves the
   SHA is an ancestor on `stocklens-data`, checks out both snapshots and runs `verify`. Locally:
   `python3 backfill_controller.py verify --before DATA_BEFORE --after DATA --dataset NAME --cohort A,B,C --before-sha SHA1 --after-sha SHA2 --as-of YYYY-MM-DD --out OUT2`.
7. **Resume only if `verification: passed`.** On a pass the report gives the next cohort or the stopping reason: go back to step 1. On a fail, stop and inspect the data branch history.

* **Pause / restart:** set the repository variable `BACKFILL_PAUSED` to `true` (or pass `--paused`); `next` and the "next" part of `verify` then report `paused`. Remove it to resume. No progress is lost,
  because none is stored apart from the data.
* **Priority:** `priority_symbols` / `--priority-symbols` / `--priority-file` list UPPER-CASE symbols to plan first. A bad entry refuses the whole plan and names its position, never its text.
  The default order is the universe file's, which is **alphabetical**. A NIFTY 500 list is optional and none is bundled (see the last limitation).
* **Cohort differences:** the printed cohort is a plan. `historical.yml` re-plans on its own day (and filters stocks Upstox does not serve); `financial_history.yml` processes the listed symbols
  stalest first and stops at its budget. `verify` therefore reports what really changed, not what was printed.

## Scheduled continuation is deliberately not configured

`backfill_next.yml` has no `schedule`, no push trigger, and no permission or secret to start anything. Automatic continuation would need a scheduler that dispatches the existing
workflows (`actions: write`) with the printed inputs; that is a separate change that needs explicit approval, a stop condition (the statuses above) and the pause variable. Not built here.

## Estimates from measured coverage (data branch as of 2026-10-25)

| Dataset | Missing / total | Calls still needed | Runs at the default budget | Runs at the ceiling |
|---|---|---|---|---|
| Historical candles | 2,533 / 2,548 | 15,198 (about 8.4 h) | about 114 (planned limit per run is below the budget) | about 6 |
| Financial history | 1,755 / 2,548 | 3,510 (about 2.2 h) | about 30 | n/a (no documented ceiling) |

Worst case with retries is up to 3x these figures. These are estimates, not promises.

## Remaining limitations

* Fundamentals, quarterly results and annual financials cannot be measured offline or continued automatically. Proposed fix (not built): a read-only export that copies the cache's state
  files into a separate artifact, so coverage can be snapshotted without running a production workflow; durable progress would need a decision on a data-branch ledger for them.
* Financial history has no attempts counter: a stock that keeps failing is deferred behind the others and retried only when nothing else is due; it is never classified permanently unavailable.
  Stocks Upstox does not list as NSE equity cost no calls but also cannot be detected offline.
* The candle workflow re-plans on the day it runs (and filters stocks Upstox does not serve), so its cohort can differ slightly from the printed one.
* `historical.yml` and `financial_history.yml` use different concurrency groups (`stocklens-historical`, `stocklens-ledger`); they save different files. This tool changes neither.
  Do not run them at the same time by hand.
* The controller cannot steer the candle workflow's own choice: a stock that keeps failing (`needs_review`) is still re-planned by `historical_batch.py` after its 3-day cooldown, at up to 6 calls each.
  The controller lists such stocks; parking them would need a change to the production selector, which is not made here.
* `calls_used` is not recorded in the data; pass it to `verify` from the run's own status summary.
* No NIFTY 500 list is bundled. Build one from NSE's `ind_nifty500list.csv` with `python3 universe.py build --mode nifty500 --csv FILE --instruments FILE --as-of DATE --out SEPARATE_FILE`
  (offline when both files are supplied; never the live `universe.json`), and review the symbols it drops before using them as a priority list.
