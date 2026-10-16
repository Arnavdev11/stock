"""Valid published fundamentals are never replaced by an empty or unusable provider answer. A 200 answer with no usable ratio, or one that silently
drops a metric the stock had, is a FAILED refresh: the previous ratios and their date stay, the failure is recorded and backs off. A partial answer
is applied when it is consistent with the contract (metrics it lists are numbers or explicitly 'no value'). A batch whose published output lost
metrics without such an explicit statement fails validation, and the values from before it stay published until it is accepted or superseded.
Offline only: a scripted session stands in for Upstox; no network is used.
Run: python3 -m unittest test_fundamentals_empty_response"""
import copy
import datetime as dt
import json
import unittest

import fundamentals_updater as fu
import validate_outputs as vo
from test_fundamentals_batches import BatchHarness
from test_fundamentals_full_universe import START, Resp, ratio_body

D = dt.timedelta(days=1)
FULL = {"pe": 12.5, "pb": 2.1, "roa": 1.2, "roe": 18.0, "roce": 21.4, "ev_ebitda": 9.9}


def body(**values):
    """A key-ratios answer listing the given metrics (by provider name) with the given company_value."""
    names = {v: k for k, v in fu.RATIO_KEYS.items()}
    return {"status": "success", "data": [{"name": names[m], "company_value": v} for m, v in values.items()]}


def published(out):
    return {r["symbol"]: r for r in out["stocks"]}


class Scripted:
    """Answers key-ratios with whatever answer() returns for the ISIN; profiles always succeed."""
    def __init__(self, h, answer):
        self.h, self.answer = h, answer
        h.provider.get = self.get

    def get(self, url, timeout=30):
        self.h.provider.requests.append(url)
        isin, kind = url.rsplit("/", 2)[-2:]
        if kind == "profile":
            return Resp(200, {"status": "success", "data": {"sector": "Some Sector"}})
        return self.answer(isin)


def base(test, n=6):
    """n stocks published with all six ratios on START; on START + 7 days they are stale and will be refreshed."""
    h = BatchHarness(test, n)
    out = h.go(cap=10 ** 6)
    return h, published(out)


class ReadRatios(unittest.TestCase):
    def test_the_answer_is_classified_without_any_request(self):
        cases = [
            ({"status": "success", "data": []}, False, "empty response (no ratios)"),
            ({"status": "success"}, False, "unusable response (no ratio list)"),
            ({"status": "success", "data": None}, False, "unusable response (no ratio list)"),
            ({"status": "success", "data": {"P/E": 3}}, False, "unusable response (no ratio list)"),
            ({"status": "success", "data": "x"}, False, "unusable response (no ratio list)"),
            ({"status": "success", "data": ["x", 3, None]}, False, "unusable response (no metric with a value)"),
            ({"status": "success", "data": [{"name": "Unknown", "company_value": "5"}]}, False, "unusable response (no metric with a value)"),
            (body(pe="-", pb="NA", roa=None, roe="", roce="--", ev_ebitda="n/a"), False, "unusable response (no metric with a value)"),
            (body(pe="abc", pb="?"), False, "unusable response (no metric with a value)"),
            (body(pe="nan", pb="inf"), False, "unusable response (no metric with a value)"),
        ]
        for b, usable, reason in cases:
            r, info = fu.read_ratios(b)
            self.assertEqual((info["usable"], info["reason"]), (usable, reason), b)
            self.assertTrue(all(v is None for v in r.values()), b)
        r, info = fu.read_ratios(ratio_body(skip=("ROCE", "EV/EBITDA")))
        self.assertEqual((info["usable"], info["explicit"], r["roce"], r["pe"]), (True, [], None, 12.5))       # omitted: not an explicit "no value"
        r, info = fu.read_ratios(body(pe="7.5", roce="-", roe="x"))
        self.assertEqual((r["pe"], info["explicit"], info["unreadable"]), (7.5, ["roce"], ["roe"]))
        r, info = fu.read_ratios(body(pe="0", roa="-3.25%"))
        self.assertEqual((r["pe"], r["roa"]), (0.0, -3.25))                                                     # zero and negative are values


class RefreshNeverLosesValidData(unittest.TestCase):
    FAILURES = {
        "empty list": lambda isin: Resp(200, {"status": "success", "data": []}),
        "no data key": lambda isin: Resp(200, {"status": "success"}),
        "data not a list": lambda isin: Resp(200, {"status": "success", "data": {"x": 1}}),
        "only unknown names": lambda isin: Resp(200, {"status": "success", "data": [{"name": "Beta", "company_value": "1"}]}),
        "all 'no value'": lambda isin: Resp(200, body(pe="-", pb="-", roa="-", roe="-", roce="-", ev_ebitda="-")),
        "unreadable values": lambda isin: Resp(200, body(pe="abc", pb="x", roa="y", roe="z", roce="?", ev_ebitda="!")),
        "status error": lambda isin: Resp(200, {"status": "error"}),
        "invalid JSON": lambda isin: type("Bad", (), {"status_code": 200, "json": lambda self: (_ for _ in ()).throw(ValueError("x"))})(),
        "HTTP 500": lambda isin: Resp(500, {}),
        "HTTP 429": lambda isin: Resp(429, {}),
        "drops metrics silently": lambda isin: Resp(200, ratio_body(skip=("ROE", "ROCE"))),
    }

    def test_every_unusable_answer_keeps_the_previous_ratios_and_date_and_backs_off(self):
        for name, answer in self.FAILURES.items():
            with self.subTest(name):
                h, before = base(self)
                Scripted(h, answer)
                out = h.go(cap=10 ** 6, day=START + 7 * D)
                after = published(out)
                self.assertEqual(after, before, name)                                         # every row byte-identical: values, missing, fetched
                for s, e in h.cache()["stocks"].items():
                    self.assertEqual((e["ratios"], e["ratios_fetched"]), (FULL, START.isoformat()), name)
                    self.assertTrue(e["last_error"].startswith("key-ratios: "), (name, e["last_error"]))
                    self.assertEqual(e["failures"], 1, name)
                    self.assertEqual(fu.state_of(e, {"isin": e["isin"]}, START + 7 * D), "failed_backoff", name)
                self.assertEqual(out["coverage"]["last_run"]["failed"], 6, name)
                self.assertEqual(out["coverage"]["last_run"]["succeeded"] + out["coverage"]["last_run"]["partial"], 0, name)
                self.assertEqual(vo.check_fundamentals(out), [], name)

    def test_the_call_cap_keeps_everything_as_it_was(self):
        h, before = base(self)
        Scripted(h, lambda isin: Resp(429, {}))
        hist = {s: {k: e.get(k) for k in ("ratios", "ratios_fetched", "last_error", "failures", "last_attempt")} for s, e in h.cache()["stocks"].items()}
        out = h.go(cap=2, day=START + 7 * D)                 # two 429 attempts, the third refused by the cap
        self.assertEqual(h.requests_this_run, 2)
        self.assertEqual(published(out), before)
        cut = [s for s, e in h.cache()["stocks"].items() if e.get("last_cut")]
        self.assertEqual(len(cut), 1)
        e = h.cache()["stocks"][cut[0]]
        self.assertEqual({k: e.get(k) for k in hist[cut[0]]}, hist[cut[0]])
        self.assertEqual(e["last_cut"]["after"], "HTTP 429")

    def test_an_empty_answer_for_a_never_fetched_stock_publishes_nothing(self):
        h = BatchHarness(self, 3)
        Scripted(h, lambda isin: Resp(200, {"status": "success", "data": []}))
        h.go(cap=10 ** 6)
        self.assertFalse((h.t / "out" / "fundamentals.json").exists())              # no ratios at all: nothing is invented, the run says so
        self.assertIn("No fundamentals could be fetched", h.log)
        self.assertTrue(all(e["last_error"] == "key-ratios: empty response (no ratios)" for e in h.cache()["stocks"].values()))


class ValidPartialAnswers(unittest.TestCase):
    def test_a_metric_the_stock_never_had_may_be_absent(self):
        h = BatchHarness(self, 4, letters="B")                # banks: no ROCE / EV-EBITDA from the start
        h.go(cap=10 ** 6)
        Scripted(h, lambda isin: Resp(200, body(pe="20", pb="3", roa="1", roe="15")))
        out = h.go(cap=10 ** 6, day=START + 7 * D)
        for r in out["stocks"]:
            self.assertEqual((r["pe"], r["roce"], r["missing"], r["fetched"]), (20.0, None, ["roce", "ev_ebitda"], (START + 7 * D).isoformat()))

    def test_an_explicit_no_value_for_a_metric_is_applied_and_new_values_are_taken(self):
        h, before = base(self, 2)
        Scripted(h, lambda isin: Resp(200, body(pe="-14.2", pb="0.9", roa="-2", roe="-", roce="NA", ev_ebitda="7")))
        out = h.go(cap=10 ** 6, day=START + 7 * D)
        for r in out["stocks"]:
            self.assertEqual((r["pe"], r["roe"], r["roce"], r["missing"]), (-14.2, None, None, ["roe", "roce"]))   # loss-making: negative values kept
        self.assertTrue(all(e["explicit_unavailable"] == ["roe", "roce"] and not e.get("last_error") for e in h.cache()["stocks"].values()))

    def test_a_metric_that_appears_is_taken(self):
        h = BatchHarness(self, 2, letters="B")
        h.go(cap=10 ** 6)
        Scripted(h, lambda isin: Resp(200, ratio_body()))
        out = h.go(cap=10 ** 6, day=START + 7 * D)
        self.assertTrue(all(r["missing"] == [] and r["roce"] == 21.4 for r in out["stocks"]))


class BatchValidationOfLostMetrics(unittest.TestCase):
    def lossy_batch(self, n=6):
        """A completed batch whose cache and output LOST roe for two stocks without any explicit 'no value' (as a defect elsewhere could)."""
        h, before = base(self, n)
        h.go(batch="1", day=START + 7 * D)                    # a sound refresh of every stock
        self.assertEqual(h.batch("1")["status"], "completed")
        c, f = h.cache(), h.t / "out" / "fundamentals.json"
        out = json.loads(f.read_text())
        bad = sorted(c["stocks"])[:2]
        for s in bad:
            c["stocks"][s]["ratios"]["roe"] = None
        for r in out["stocks"]:
            if r["symbol"] in bad:
                r["roe"], r["missing"] = None, ["roe"]
        out["coverage"]["metrics_complete"] -= 2; out["coverage"]["metrics_partial"] += 2; out["coverage"]["metric_available"]["roe"] -= 2
        (h.t / "data" / "c.json").write_text(json.dumps(c)); f.write_text(json.dumps(out))
        return h, before, bad

    def test_a_batch_that_removes_metrics_fails_and_the_previous_values_stay_published(self):
        h, before, bad = self.lossy_batch()
        self.assertEqual(h.validate("1", previous={"stocks": list(before.values())}), 1)
        for s in bad:
            self.assertIn("%s: metric(s) roe disappeared from the published row" % s, h.offline_log)
        b = h.batch("1")
        self.assertEqual((b["status"], b["validation"]["publish_holds"]), ("failed_validation", bad))
        out = json.loads((h.t / "out" / "fundamentals.json").read_text())
        rows = published(out)
        for s in bad:
            self.assertEqual(rows[s], before[s])               # what the next publish shows: the values AND date from before the batch
        self.assertEqual(vo.check_fundamentals(out), [])
        h.go(batch="2", day=START + 7 * D)                     # and the next batch is blocked
        self.assertEqual(h.rc, 1)

    def test_an_explicit_acceptance_releases_the_new_values(self):
        h, before, bad = self.lossy_batch()
        h.validate("1", previous={"stocks": list(before.values())})
        self.assertEqual(h.accept("1", reason="roe withdrawn by the provider, checked by hand"), 0, h.offline_log)
        rows = published(json.loads((h.t / "out" / "fundamentals.json").read_text()))
        self.assertTrue(all(rows[s]["roe"] is None for s in bad))
        self.assertFalse(any(e.get("publish_hold") for e in h.cache()["stocks"].values()))

    def test_a_later_sound_refresh_supersedes_the_hold(self):
        h, before, bad = self.lossy_batch()
        h.validate("1", previous={"stocks": list(before.values())})       # failed: the two stocks are held with their values from before
        c = h.cache()
        self.assertTrue(all(c["stocks"][s]["publish_hold"]["ratios"] == FULL for s in bad))
        for s in bad:
            c["stocks"][s]["ratios_fetched"] = START.isoformat()            # stale again, so the scheduled run refreshes them
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        out = h.go(cap=10 ** 6, day=START + 14 * D)            # the provider answers with all six metrics again
        rows = published(out)
        self.assertTrue(all(rows[s]["roe"] == 18.0 and rows[s]["fetched"] == (START + 14 * D).isoformat() for s in bad))
        self.assertFalse(any(e.get("publish_hold") for e in h.cache()["stocks"].values()))

    def test_a_refresh_that_would_lose_a_held_metric_keeps_the_hold(self):
        h, before, bad = self.lossy_batch()
        h.validate("1", previous={"stocks": list(before.values())})
        c = h.cache()
        for s in bad:
            c["stocks"][s]["ratios_fetched"] = START.isoformat()
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        Scripted(h, lambda isin: Resp(200, ratio_body(skip=("ROE",))))     # still no ROE, and no explicit "no value"
        out = h.go(cap=10 ** 6, day=START + 14 * D)
        rows = published(out)
        for s in bad:
            self.assertEqual(rows[s], before[s])
            self.assertIn("drops previously available metric(s) roe", h.cache()["stocks"][s]["last_error"])

    def test_a_batch_with_legitimate_changes_passes(self):
        h, before = base(self, 6)
        names = sorted(before)
        answers = {before[names[0]]["isin"]: Resp(200, body(pe="31.7", pb="4", roa="2", roe="19", roce="22", ev_ebitda="10")),   # new values
                   before[names[1]]["isin"]: Resp(200, body(pe="-8", pb="1", roa="-4", roe="-9", roce="-6", ev_ebitda="-3")),    # turned loss-making
                   before[names[2]]["isin"]: Resp(200, body(pe="-", pb="1.1", roa="1", roe="12", roce="14", ev_ebitda="8"))}     # P/E explicitly n/a
        Scripted(h, lambda isin: answers.get(isin) or Resp(200, ratio_body()))
        out = h.go(batch="1", day=START + 7 * D)
        self.assertEqual(h.validate("1", previous={"stocks": list(before.values())}), 0, h.offline_log)
        rows = published(out)
        self.assertEqual((rows[names[0]]["pe"], rows[names[1]]["pe"], rows[names[2]]["pe"], rows[names[2]]["missing"]), (31.7, -8.0, None, ["pe"]))

    def test_a_batch_of_empty_answers_fails_and_changes_nothing_published(self):
        h, before = base(self, 6)
        Scripted(h, lambda isin: Resp(200, {"status": "success", "data": []}))
        out = h.go(batch="1", day=START + 7 * D)
        self.assertEqual(published(out), before)
        self.assertEqual(fu._count_outcomes(h.batch("1")), {"failed": 6})
        self.assertEqual(h.validate("1", previous={"stocks": list(before.values())}), 1)
        self.assertIn("no successful update", h.offline_log)


if __name__ == "__main__":
    unittest.main()
