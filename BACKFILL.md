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

`improved` (new data for a stock that had none or new years) - `refreshed` (newer candles or a newer fetch) - `unchanged_up_to_date` - `retryable_failed` (failed, will be retried after
the cooldown / later in the order) - `needs_review` (candles: failed 3 or more times, `attempts.json`) - `still_missing` - `regressed` (saved data lost or shortened: **verification fails**,
the next step is blocked, nothing should be published).

Financial history revisions follow the repository's own rule (`ledger_storage.no_loss_problems`): a changed value is fine only if the old value is recorded as a revision; a lost year
or a value turning null is a regression. Consolidated and standalone year records are keyed separately by that same check.

## Stop conditions

`nothing_to_do` (every stock up to date or damaged), `waiting_cooldown` (everything left failed recently; the wake-up date is printed), `paused`, `not_automatic`, a refusal (bad input,
unreadable ledger, budget above the cap), or `verification: failed`. There is no recursion: no run starts another.

## How to operate it

All commands are read-only. Use a checkout of the `stocklens-data` branch (here `DATA`).

```
# dry run: prints the plan and writes nothing
python3 backfill_controller.py next --data-branch DATA --dataset historical_candles --as-of 2026-10-25 --out OUT --dry-run

# plan one bounded run (writes OUT/backfill_next.json and .md only)
python3 backfill_controller.py next --data-branch DATA --dataset financial_history --as-of 2026-10-25 --out OUT --max-calls 120 [--priority-file FILE]

# after the existing workflow has finished and saved: verify, with DATA_BEFORE = a checkout of the data branch from before the run
python3 backfill_controller.py verify --before DATA_BEFORE --after DATA --plan OUT/backfill_next.json --as-of 2026-10-25 --out OUT2 [--calls-used N]
```

Or run **Backfill - plan or verify the next run (read-only)** from the Actions tab (mode `next`, then `verify` with the `before_ref` SHA of `stocklens-data` recorded before the run).

* **One bounded run:** plan, start the printed existing workflow by hand with the printed inputs, verify.
* **Resume:** plan again; it starts from the real coverage.
* **Pause:** set the repository variable `BACKFILL_PAUSED` to `true` (or pass `--paused`); every plan then returns `paused`. **Restart:** remove it or set it to anything else. No progress is lost,
  because none is stored apart from the data.
* **Priority:** `--priority-file` (or the workflow's `priority_symbols`) lists symbols to plan first. Every symbol must be in the universe, or the plan is refused. The default order is the
  universe file's, which is **alphabetical**, so "the first 500" are not the largest companies. A NIFTY 500 priority list must come from NSE's official list (see below); none is bundled.

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
* `calls_used` is not recorded in the data; pass it to `verify` from the run's own status summary.
* No NIFTY 500 list is bundled. Build one from NSE's `ind_nifty500list.csv` with `python3 universe.py build --mode nifty500 --csv FILE --instruments FILE --as-of DATE --out SEPARATE_FILE`
  (offline when both files are supplied; never the live `universe.json`), and review the symbols it drops before using them as a priority list.
