"""Safety rules for the fundamentals batches: failure-rate limit, batch-specific acceptance (no blanket override), ISIN identity (a changed ISIN is held
aside, never refetched over or deleted), each symbol counted once, failure history kept when the call cap cuts a retry, and every API attempt persisted
before it is sent. Offline only: the scripted provider of test_fundamentals_full_universe stands in for Upstox.
Run: python3 -m unittest test_fundamentals_batch_safety"""
import copy
import datetime as dt
import json
import os
import unittest
from unittest import mock

import fundamentals_updater as fu
import validate_outputs as vo
from test_fundamentals_batches import BatchHarness
from test_fundamentals_full_universe import START

D = dt.timedelta(days=1)


def published(out):
    return {r["symbol"]: r for r in out["stocks"]}


def with_base(test, n, letters):
    """A harness whose daily run has already published the stocks that serve data (the real situation before a batch)."""
    h = BatchHarness(test, n, letters=letters)
    h.go(cap=10 ** 6)
    return h


class FailureRate(unittest.TestCase):
    def test_a_batch_in_which_every_stock_failed_fails_validation_and_blocks_the_next(self):
        h = with_base(self, 30, "AF")                        # 15 published, 15 always HTTP 500
        h.go(batch="1", max_stocks=15, day=START + 40 * D)   # the failed ones are due again first: all 15 fail
        self.assertEqual(fu._count_outcomes(h.batch("1")), {"failed": 15})
        self.assertEqual(h.validate("1"), 1)
        self.assertIn("no successful update: 15 planned, 15 attempted, 15 failed", h.offline_log)
        self.assertEqual(h.validate("1", rate=1.0), 1)       # no maximum rate lets a batch with no success pass
        self.assertEqual(h.batch("1")["status"], "failed_validation")
        h.go(batch="2", max_stocks=15, day=START + 40 * D)
        self.assertEqual(h.rc, 1)
        self.assertEqual(h.requests_this_run, 0)

    def test_the_maximum_rate_is_configurable_and_inclusive(self):
        h = BatchHarness(self, 10, letters="AAAAAAAAAF")     # 1 of 10 fails = 10%
        h.go(batch="1")
        self.assertEqual(h.validate("1"), 0, h.offline_log)  # exactly the default 10%: passes
        h2 = BatchHarness(self, 10, letters="AAAAAAAAFF")    # 2 of 10 = 20%
        h2.go(batch="1")
        self.assertEqual(h2.validate("1"), 1)
        with mock.patch.dict(os.environ, {"FUNDAMENTALS_MAX_FAILURE_RATE": "0.25"}):
            self.assertEqual(h2.validate("1"), 0, h2.offline_log)
        self.assertEqual(h2.batch("1")["validation"]["max_failure_rate"], 0.25)

    def test_an_unusable_rate_is_refused(self):
        for bad in ("abc", "1.5", "-0.1", "nan"):
            with self.assertRaises(SystemExit):
                fu.max_failure_rate(bad)
        self.assertEqual(fu.max_failure_rate(None), fu.DEFAULT_MAX_FAILURE_RATE)

    def test_a_batch_whose_planned_stocks_were_all_skipped_has_no_success_and_fails(self):
        h = BatchHarness(self, 20)
        h.go(batch="1", cap=10)                              # 5 stocks done, interrupted
        h.go(cap=10 ** 6)                                    # the scheduled run refreshes everything else meanwhile
        h.go(batch="1")                                      # resumes: the rest are fresh now
        oc = fu._count_outcomes(h.batch("1"))
        self.assertEqual(oc, {"ok": 5, "skipped_fresh": 15})
        self.assertEqual(h.validate("1"), 0, h.offline_log)  # 5 real successes: passes
        h3 = BatchHarness(self, 20)
        h3.go(batch="1", cap=1)                              # one attempt, nothing finished
        h3.go(cap=10 ** 6)
        h3.go(batch="1")
        self.assertEqual(fu._count_outcomes(h3.batch("1")), {"skipped_fresh": 20})
        self.assertEqual(h3.validate("1"), 1)
        self.assertIn("no successful update", h3.offline_log)


class Acceptance(unittest.TestCase):
    def failed_batch(self):
        h = with_base(self, 30, "AF")
        h.go(batch="1", max_stocks=15, day=START + 40 * D)
        self.assertEqual(h.validate("1"), 1)
        return h

    def test_there_is_no_blanket_override_any_more(self):
        h = self.failed_batch()
        with mock.patch.dict(os.environ, {"FUNDAMENTALS_ALLOW_UNVALIDATED": "yes"}):
            h.go(batch="2", day=START + 40 * D)
        self.assertEqual(h.rc, 1)
        self.assertIn("not validated or accepted", h.log)
        self.assertNotIn("ALLOW_UNVALIDATED", (fu.__file__ and open(fu.__file__, encoding="utf-8").read()))

    def test_an_acceptance_names_one_batch_who_and_why_and_changes_no_stock(self):
        h = self.failed_batch()
        stocks_before = copy.deepcopy(h.cache()["stocks"])
        self.assertEqual(h.accept("1", by="pushp", reason="provider outage confirmed; stocks keep their old data"), 0, h.offline_log)
        b = h.batch("1")
        self.assertEqual(b["status"], "accepted")
        a = b["acceptance"]
        self.assertEqual((a["by"], a["status_before"], a["validation_result"], a["outcomes"]), ("pushp", "failed_validation", "failed", {"failed": 15}))
        self.assertIn("no successful update", a["validation_problems"][0])
        self.assertEqual(h.cache()["stocks"], stocks_before)
        h.go(batch="2", max_stocks=15, day=START + 40 * D)
        self.assertEqual(h.rc, 0, h.log)
        h.go(batch="1", day=START + 40 * D)                  # an accepted batch never runs again
        self.assertEqual(h.rc, 1)
        self.assertEqual(h.accept("1"), 1)                   # nor is it accepted twice

    def test_acceptance_needs_a_name_and_a_reason_and_only_applies_to_failed_or_interrupted_batches(self):
        h = self.failed_batch()
        self.assertEqual(h.accept("1", by="", reason="a good enough reason"), 1)
        self.assertEqual(h.accept("1", by="pushp", reason="short"), 1)
        self.assertEqual(h.batch("1")["status"], "failed_validation")
        g = BatchHarness(self, 10)
        g.go(batch="1")
        self.assertEqual(g.accept("1"), 1)                   # completed: it must be validated, not accepted
        self.assertIn("only a batch that failed validation or was interrupted", g.offline_log)
        self.assertEqual(g.validate("1"), 0)
        self.assertEqual(g.accept("1"), 1)                   # validated: nothing to accept
        self.assertEqual(g.accept("9"), 1)                   # unknown id

    def test_an_interrupted_batch_can_be_accepted_and_its_unfinished_stocks_stay_eligible(self):
        h = BatchHarness(self, 20)
        h.go(batch="1", cap=10)
        self.assertEqual(h.batch("1")["status"], "interrupted")
        self.assertEqual(h.accept("1", reason="stopping this batch; the rest goes into batch 2"), 0)
        h.go(batch="2")
        self.assertEqual(h.rc, 0, h.log)
        self.assertEqual(len(h.batch("2")["planned"]), 15)

    def test_acceptance_is_per_batch(self):
        h = BatchHarness(self, 40, letters="F")
        h.go(batch="1", max_stocks=10)
        h.validate("1")
        h.accept("1", reason="first outage batch accepted after review")
        h.go(batch="2", max_stocks=10)
        self.assertEqual(h.validate("2"), 1)
        h.go(batch="3", max_stocks=10)                       # batch 2 is not covered by batch 1's acceptance
        self.assertEqual(h.rc, 1)
        self.assertIn("batch 2 is failed_validation", h.log)

    def test_a_corrected_failure_can_be_validated_again(self):
        h = BatchHarness(self, 20)
        h.go(batch="1")
        f = h.t / "out" / "fundamentals.json"
        good = f.read_text()
        d = json.loads(good); d["stocks"][0]["pe"] = 1.0; f.write_text(json.dumps(d))
        self.assertEqual(h.validate("1"), 1)
        f.write_text(good)
        self.assertEqual(h.validate("1"), 0, h.offline_log)
        self.assertEqual(h.batch("1")["status"], "validated")


class IsinIdentity(unittest.TestCase):
    def test_a_changed_isin_is_held_aside_unchanged_not_published_and_not_refetched(self):
        h = BatchHarness(self, 10)
        before = h.go(cap=10 ** 6)
        sym = "A0003"
        old = copy.deepcopy(h.cache()["stocks"][sym])
        h.instruments[sym] = dict(h.instruments[sym], isin="INE999999991")
        out = h.go(batch="1", day=START + 8 * D)             # everything is stale; the changed one must not be asked for under either ISIN
        self.assertFalse(any("INE999999991" in u or old["isin"] in u for u in h.provider.requests[-h.requests_this_run:]))
        self.assertNotIn(sym, published(out))
        e = h.cache()["stocks"][sym]
        self.assertEqual(e["held_isin_change"]["record"], {k: old[k] for k in fu.HELD_KEYS if k in old})
        self.assertEqual(out["coverage"]["categories"]["held_isin_change"], 1)
        self.assertNotIn(sym, h.batch("1")["planned"])        # held: not planned, so never fetched
        self.assertEqual(h.validate("1", previous=before), 0, h.offline_log)   # a hold is reported, not a disappearance
        self.assertIn("A0003 is held aside", h.offline_log)
        h.instruments[sym] = dict(h.instruments[sym], isin=old["isin"])      # the list gives the old ISIN again
        out = h.go(cap=0, day=START + 8 * D)                 # no call needed (or allowed) to restore it
        self.assertEqual(h.requests_this_run, 0)
        self.assertEqual({m: published(out)[sym][m] for m in fu.METRICS}, old["ratios"])   # restored as it was, published again
        self.assertEqual((published(out)[sym]["isin"], published(out)[sym]["fetched"]), (old["isin"], old["ratios_fetched"]))
        self.assertNotIn("held_isin_change", h.cache()["stocks"][sym])

    def test_validation_rejects_a_published_isin_that_differs_from_the_cache_or_the_list(self):
        h = BatchHarness(self, 10)
        h.go(batch="1")
        f = h.t / "out" / "fundamentals.json"
        good = f.read_text()
        d = json.loads(good); d["stocks"][0]["isin"] = "INE999999999"; f.write_text(json.dumps(d))
        self.assertEqual(h.validate("1"), 1)
        self.assertIn("published under ISIN INE999999999 but the cache holds", h.offline_log)
        f.write_text(good)
        c = h.cache(); s = d["stocks"][1]["symbol"]
        c["stocks"][s]["listed_isin"] = "INE888888888"
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        self.assertEqual(h.validate("1"), 1)
        self.assertIn("it should have been held aside", h.offline_log)

    def test_a_record_without_data_simply_follows_the_new_isin_and_the_change_is_recorded(self):
        h = BatchHarness(self, 3, letters="F")
        h.go(cap=10 ** 6)                                    # fails: an ISIN but no ratios
        sym = "F0001"
        old = h.cache()["stocks"][sym]["isin"]
        h.instruments[sym] = dict(h.instruments[sym], isin="INE777777771")
        h.provider.by_isin["INE777777771"] = "ok"
        out = h.go(cap=10 ** 6, day=START + 2 * D)
        self.assertEqual(published(out)[sym]["isin"], "INE777777771")
        self.assertEqual(h.cache()["stocks"][sym]["isin_history"], [{"day": (START + 2 * D).isoformat(), "from": old, "to": "INE777777771"}])

    def test_a_universe_isin_difference_is_a_notice_not_a_failure(self):
        h = BatchHarness(self, 10)
        doc = json.loads((h.t / "u.json").read_text())
        doc["symbols"][2]["isin"] = "INE555555551"           # the directory snapshot disagrees with the instrument list (as for BANSALWIRE)
        (h.t / "u.json").write_text(json.dumps(doc))
        h.go(batch="1")
        self.assertEqual(h.validate("1"), 0, h.offline_log)
        self.assertIn("differs from the universe file's INE555555551 (informational)", h.offline_log)
        self.assertIn(doc["symbols"][2]["symbol"], h.batch("1")["validation"]["notices"]["isin_differs_from_universe"])


class CountedOnce(unittest.TestCase):
    def test_a_published_stock_that_left_the_instrument_list_is_counted_once(self):
        h = BatchHarness(self, 5)
        h.go(cap=50)
        h.instruments.pop("A0002")
        out = h.go(cap=50, day=START + D)
        cov = out["coverage"]
        self.assertIn("A0002", published(out))               # its valid data is kept ...
        self.assertEqual([e["symbol"] for e in out["errors"]], ["A0002"])
        self.assertEqual((cov["published"], cov["not_in_upstox"], cov["published_not_listed"]), (5, 0, 1))   # ... and it is not double-counted
        self.assertEqual(sum(cov["categories"].values()), cov["universe"])
        self.assertEqual(vo.check_fundamentals(out, {"A%04d" % i for i in range(5)}), [])
        self.assertEqual(h.cache()["stocks"]["A0002"]["not_listed_since"], (START + D).isoformat())
        h.instruments["A0002"] = {"isin": h.cache()["stocks"]["A0002"]["isin"], "name": "STOCK A0002"}
        h.go(cap=50, day=START + 2 * D)
        self.assertNotIn("not_listed_since", h.cache()["stocks"]["A0002"])

    def test_the_categories_must_sum_to_the_universe_and_old_files_still_validate(self):
        h = BatchHarness(self, 6, letters="AE")
        out = h.go(cap=50)
        self.assertEqual(vo.check_fundamentals(out), [])
        bad = copy.deepcopy(out); bad["coverage"]["categories"]["failed"] += 1
        self.assertIn("coverage.categories add up to 7, not the universe of 6", vo.check_fundamentals(bad))
        bad = copy.deepcopy(out); bad["coverage"]["not_in_upstox"] += 1; bad["coverage"]["categories"]["not_in_upstox"] += 1; bad["coverage"]["categories"]["failed"] -= 1
        self.assertIn("coverage: published + not_in_upstox + not_yet_fetched does not equal the universe", vo.check_fundamentals(bad))
        old = copy.deepcopy(out); del old["coverage"]["categories"]; old["coverage"]["not_in_upstox"] += 1   # a file written before the partition (like 10-16)
        self.assertEqual(vo.check_fundamentals(old), [])

    def test_duplicate_rows_errors_and_outside_symbols_are_rejected(self):
        h = BatchHarness(self, 4, letters="AE")
        out = h.go(cap=50)
        d = copy.deepcopy(out); d["stocks"].append(copy.deepcopy(d["stocks"][0]))
        self.assertTrue(any("lists a stock more than once" in x for x in vo.check_fundamentals(d)))
        d = copy.deepcopy(out); d["errors"].append(copy.deepcopy(d["errors"][0]))
        self.assertTrue(any("lists an error for a stock more than once" in x for x in vo.check_fundamentals(d)))
        self.assertTrue(any("outside the universe" in x for x in vo.check_fundamentals(out, {"A0000"})))

    def test_a_universe_with_a_duplicate_symbol_stops_the_run_before_any_call(self):
        h = BatchHarness(self, 4)
        with mock.patch.object(fu.universe, "load", lambda env=None: ["A0000", "A0001", "A0001", "A0003"]):
            h.go(cap=50)
        self.assertEqual((h.rc, h.requests_this_run), (1, 0))
        self.assertIn("lists 1 symbol(s) more than once (A0001)", h.log)


class CapCutKeepsHistory(unittest.TestCase):
    def test_a_retry_cut_by_the_cap_keeps_the_failure_count_error_and_back_off(self):
        h = BatchHarness(self, 1, letters="F")
        h.go(cap=50)
        before = {k: h.cache()["stocks"]["F0000"][k] for k in ("last_error", "failures", "last_attempt")}
        h.go(cap=2, day=START + D)                           # due again: two attempts fail, the third is refused by the cap
        e = h.cache()["stocks"]["F0000"]
        self.assertEqual({k: e[k] for k in before}, before)
        self.assertEqual(e["last_cut"], {"day": (START + D).isoformat(), "by": "call cap", "after": "HTTP 500"})
        self.assertEqual(fu.state_of(e, {"isin": e["isin"]}, START + D), "failed_due")       # still a failure due for retry, not a fresh 'missing'
        h.go(cap=50, day=START + 2 * D)
        self.assertEqual(h.cache()["stocks"]["F0000"]["failures"], 2)                       # the back-off continues from where it was
        self.assertEqual(fu.state_of(h.cache()["stocks"]["F0000"], {"isin": e["isin"]}, START + 3 * D), "failed_backoff")


class EveryAttemptPersisted(unittest.TestCase):
    def test_a_crashed_run_is_never_under_counted_and_resume_skips_nothing(self):
        h = BatchHarness(self, 120)
        real = h.provider.get
        def dying(url, timeout=30):
            if len(h.provider.requests) >= 130:              # killed during the 131st request: 65 stocks done, last checkpoint after 50
                raise KeyboardInterrupt
            return real(url, timeout)
        h.provider.get = dying
        with self.assertRaises(KeyboardInterrupt):
            h.go(batch="1", max_stocks=120)
        with mock.patch.object(fu, "CACHE_FILE", h.t / "data" / "c.json"):
            self.assertEqual(fu.logged_calls(), {"1#1": 131})                               # the attempt that was in flight is counted too
        self.assertEqual(h.batch("1")["runs"][0]["calls"], 100)                              # the checkpoint alone would say 100
        h.provider.get = real
        h.go(batch="1", max_stocks=120)
        b = h.batch("1")
        self.assertEqual([r["calls"] for r in b["runs"]], [131, 140])                        # corrected on resume
        self.assertEqual(sum(r["calls"] for r in b["runs"]), len(h.provider.requests) + 1)  # every request, plus the one killed before it was answered
        self.assertTrue(all(b["outcomes"].get(s) in fu.FINAL_OUTCOMES for s in b["planned"]))
        self.assertEqual(h.validate("1"), 0, h.offline_log)

    def test_validation_sees_a_run_over_its_cap_from_the_log(self):
        h = BatchHarness(self, 20)
        h.go(batch="1", cap=1200)
        with open(h.t / "data" / "fundamentals_calls.jsonl", "a") as f:
            f.write(json.dumps({"run": "1#1", "calls": 5000}) + "\n")
        self.assertEqual(h.validate("1"), 1)
        self.assertIn("run 1 of the batch used 5000 calls, more than its cap of 1200", h.offline_log)

    def test_a_torn_last_line_is_ignored(self):
        h = BatchHarness(self, 4)
        h.go(batch="1")
        with open(h.t / "data" / "fundamentals_calls.jsonl", "a") as f:
            f.write('{"run": "1#1", "cal')
        self.assertEqual(h.validate("1"), 0, h.offline_log)


class PreservesExistingRecords(unittest.TestCase):
    def test_records_outside_the_batch_are_published_unchanged(self):
        h = BatchHarness(self, 60, letters="AB")
        base = h.go(cap=10 ** 6)                             # 60 published (complete and partial)
        c = h.cache()
        for s in sorted(c["stocks"])[:20]:
            c["stocks"][s]["ratios_fetched"] = (START - 10 * D).isoformat()   # 20 stale
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        before = published(h.go(cap=0))                       # republish without calls
        out = h.go(batch="1", max_stocks=500, day=START + D)
        self.assertEqual(sorted(h.batch("1")["planned"]), sorted(c["stocks"])[:20])
        after = published(out)
        for s, r in before.items():
            if s not in h.batch("1")["planned"]:
                self.assertEqual(after[s], r)                # untouched stocks: byte-identical rows, nulls kept as nulls
        self.assertEqual(set(after), set(published(base)))
        self.assertEqual(h.validate("1", previous={"stocks": list(before.values())}), 0, h.offline_log)


if __name__ == "__main__":
    unittest.main()
