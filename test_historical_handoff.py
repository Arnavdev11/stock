"""
historical_handoff.py and the historical-data hand-over workflows. SYNTHETIC data only (invented prices); no network, no token, no workflow is run.
They prove: a hand-over can never silently delete or alter saved history, can write only under historical/, and carries no credential.
"""
import copy
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import historical_handoff as H
import shards

ROOT = Path(__file__).parent
WF = ROOT / ".github" / "workflows"


def candles(n=60, start="2026-06-01", base=100.0):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        d = d0 + dt.timedelta(days=i)
        px = base + i
        out.append({"date": d.isoformat(), "open": px, "high": px + 2, "low": px - 1, "close": px + 1, "volume": 1000 + i})
    return out


def entry(sym, cs):
    return {"symbol": sym, "isin": "INE000000000", "instrument_key": "NSE_EQ|" + sym, "candles": cs}


META = {"updated": "2026-10-10", "source": "Upstox", "interval": "1day", "notes": {"candles": "synthetic"}}


def make_set(root, stocks, bench=None, with_main=True):
    """stocks: {SYM: candles}. Writes historical.json and shards/ in the hand-over layout."""
    root = Path(root)
    (root / "shards").mkdir(parents=True, exist_ok=True)
    for sym, cs in stocks.items():
        (root / "shards" / (sym + ".json")).write_text(json.dumps(dict(META, stocks={sym: entry(sym, cs)})), encoding="utf-8")
    if with_main:
        doc = dict(META, benchmark=({"symbol": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50", "candles": bench} if bench else None),
                   stocks={s: entry(s, c) for s, c in stocks.items()}, errors=[])
        (root / "historical.json").write_text(json.dumps(doc), encoding="utf-8")


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def d(self, name):
        p = self.t / name
        return p

    def has(self, problems, text):
        self.assertTrue(any(text in p for p in problems), problems)


class Verify(Tmp):
    def test_a_good_first_hand_over_passes(self):
        make_set(self.d("new"), {"TCS": candles()}, bench=candles(base=500))
        self.assertEqual(H.verify(self.d("new"), self.d("nothing_saved_yet")), [])

    def test_nothing_to_hand_over(self):
        self.d("new").mkdir()
        self.has(H.verify(self.d("new"), None), "nothing to hand over")

    def test_extending_history_passes(self):
        make_set(self.d("old"), {"TCS": candles(60)}, bench=candles(60, base=500))
        make_set(self.d("new"), {"TCS": candles(75)}, bench=candles(75, base=500))
        self.assertEqual(H.verify(self.d("new"), self.d("old")), [])

    def test_a_lost_day_is_refused(self):
        make_set(self.d("old"), {"TCS": candles(60)})
        cs = candles(60)
        del cs[20]
        make_set(self.d("new"), {"TCS": cs})
        p = H.verify(self.d("new"), self.d("old"))
        self.has(p, "would be lost")
        self.assertTrue(any("shards/TCS.json" in x for x in p) and any("historical.json TCS" in x for x in p))

    def test_a_vanishing_stock_is_refused(self):
        make_set(self.d("old"), {"TCS": candles(), "INFY": candles()})
        make_set(self.d("new"), {"TCS": candles()})
        p = H.verify(self.d("new"), self.d("old"))
        self.has(p, "shards/INFY.json would disappear")
        self.has(p, "INFY would disappear")

    def test_a_vanishing_benchmark_is_refused(self):
        make_set(self.d("old"), {"TCS": candles()}, bench=candles(base=500))
        make_set(self.d("new"), {"TCS": candles()}, bench=None)
        self.has(H.verify(self.d("new"), self.d("old")), "would disappear")

    def test_a_missing_main_file_is_refused(self):
        make_set(self.d("old"), {"TCS": candles()})
        make_set(self.d("new"), {"TCS": candles()}, with_main=False)
        self.has(H.verify(self.d("new"), self.d("old")), "historical.json is missing")

    def test_the_last_date_cannot_move_backwards_and_nothing_can_be_shortened(self):
        make_set(self.d("old"), {"TCS": candles(60)})
        make_set(self.d("new"), {"TCS": candles(40)})
        self.has(H.verify(self.d("new"), self.d("old")), "would be lost")

    def test_an_old_candle_cannot_be_altered_but_the_recent_days_may_be_corrected(self):
        make_set(self.d("old"), {"TCS": candles(60)})
        cs = candles(60)
        cs[3] = dict(cs[3], close=cs[3]["close"] + 5)                 # 56 days before the last: altered
        make_set(self.d("new"), {"TCS": cs})
        self.has(H.verify(self.d("new"), self.d("old")), "were altered")
        cs = candles(60)
        cs[-2] = dict(cs[-2], close=cs[-2]["close"] + 0.5)              # 1 day before the last: a correction, allowed
        shutil_tree = self.d("new2")
        make_set(shutil_tree, {"TCS": cs})
        self.assertEqual(H.verify(shutil_tree, self.d("old")), [])

    def test_bad_candles_are_refused(self):
        cases = {
            "not ascending": lambda cs: cs.reverse(),
            "duplicate date": lambda cs: cs.__setitem__(5, dict(cs[4])),
            "negative price": lambda cs: cs[2].update(open=-1),
            "zero price": lambda cs: cs[2].update(close=0),
            "high below low": lambda cs: cs[2].update(high=1, low=50),
            "bad volume": lambda cs: cs[2].update(volume=True),
            "negative volume": lambda cs: cs[2].update(volume=-5),
            "float volume": lambda cs: cs[2].update(volume=1.5),
            "bad date": lambda cs: cs[2].update(date="2026-13-40"),
            "short date": lambda cs: cs[2].update(date="2026-6-3"),
            "extra key": lambda cs: cs[2].update(oi=1),
            "missing key": lambda cs: cs[2].pop("volume"),
            "string price": lambda cs: cs[2].update(open="100"),
        }
        for name, mutate in cases.items():
            cs = candles()
            mutate(cs)
            root = self.d("n_" + re.sub(r"\W", "_", name))
            make_set(root, {"TCS": cs})
            self.assertNotEqual(H.verify(root, None), [], name)

    def test_null_volume_is_allowed(self):
        cs = candles()
        cs[3]["volume"] = None
        make_set(self.d("new"), {"TCS": cs})
        self.assertEqual(H.verify(self.d("new"), None), [])

    def test_empty_candle_list_is_refused(self):
        make_set(self.d("new"), {"TCS": []})
        self.has(H.verify(self.d("new"), None), "no candles")

    def test_unexpected_files_folders_and_names_are_refused(self):
        make_set(self.d("new"), {"TCS": candles()})
        (self.d("new") / "notes.txt").write_text("x")
        (self.d("new") / "other").mkdir()
        (self.d("new") / "shards" / "bad name!.json").write_text("{}")
        (self.d("new") / "shards" / "TCS.txt").write_text("x")
        p = H.verify(self.d("new"), None)
        self.has(p, "unexpected file notes.txt")
        self.has(p, "unexpected folder other")
        self.has(p, "not a valid symbol file name")
        self.has(p, "unexpected file shards/TCS.txt")

    def test_a_symbolic_link_is_refused(self):
        make_set(self.d("new"), {"TCS": candles()})
        os.symlink("/etc/hostname", self.d("new") / "shards" / "EVIL.json")
        self.has(H.verify(self.d("new"), None), "symbolic link")

    def test_a_shard_must_hold_exactly_its_own_symbol(self):
        make_set(self.d("new"), {"TCS": candles()})
        p = self.d("new") / "shards" / "TCS.json"
        doc = json.loads(p.read_text())
        doc["stocks"]["INFY"] = entry("INFY", candles())
        p.write_text(json.dumps(doc))
        self.has(H.verify(self.d("new"), None), "exactly its own symbol")
        make_set(self.d("n2"), {"TCS": candles()})
        q = self.d("n2") / "shards" / "TCS.json"
        doc = json.loads(q.read_text())
        doc["stocks"]["TCS"]["symbol"] = "INFY"
        q.write_text(json.dumps(doc))
        self.has(H.verify(self.d("n2"), None), "exactly its own symbol")

    def test_invalid_json_is_refused(self):
        make_set(self.d("new"), {"TCS": candles()})
        (self.d("new") / "historical.json").write_text("{not json")
        self.has(H.verify(self.d("new"), None), "not valid JSON")

    def test_credentials_are_refused_anywhere_in_the_files(self):
        for secret in ("Bearer abc123", "eyJhbGciOiJIUzI1NiJ9.payload", "wss://example/feed", "access_token", "Authorization: x", "UPSTOX_ANALYTICS_TOKEN", "api_key"):
            root = self.d("s_" + re.sub(r"\W", "_", secret))
            make_set(root, {"TCS": candles()})
            doc = json.loads((root / "historical.json").read_text())
            doc["errors"] = [{"symbol": "TCS", "error": "HTTP 400 " + secret}]
            (root / "historical.json").write_text(json.dumps(doc))
            self.has(H.verify(root, None), "credential")
        shard_root = self.d("s_shard")
        make_set(shard_root, {"TCS": candles()})
        sp = shard_root / "shards" / "TCS.json"
        sp.write_text(sp.read_text().replace("synthetic", "Bearer zzz"))
        self.has(H.verify(shard_root, None), "credential")

    def test_the_ordinary_word_token_is_not_a_false_alarm(self):
        make_set(self.d("new"), {"TCS": candles()})
        doc = json.loads((self.d("new") / "historical.json").read_text())
        doc["errors"] = [{"symbol": "TCS", "error": "token bucket rate limit"}]
        (self.d("new") / "historical.json").write_text(json.dumps(doc))
        self.assertEqual(H.verify(self.d("new"), None), [])

    def test_size_and_count_caps(self):
        make_set(self.d("new"), {"TCS": candles(), "INFY": candles()})
        old = (H.MAX_FILES, H.MAX_TOTAL_BYTES, H.MAX_FILE_BYTES)
        try:
            H.MAX_FILES = 2
            self.has(H.verify(self.d("new"), None), "more than 2 files")
            H.MAX_FILES = old[0]
            H.MAX_TOTAL_BYTES = 100
            self.has(H.verify(self.d("new"), None), "bytes in all")
            H.MAX_TOTAL_BYTES = old[1]
            H.MAX_FILE_BYTES = 100
            self.has(H.verify(self.d("new"), None), "larger than")
        finally:
            H.MAX_FILES, H.MAX_TOTAL_BYTES, H.MAX_FILE_BYTES = old

    def test_a_damaged_saved_copy_blocks_the_save_instead_of_being_overwritten(self):
        make_set(self.d("old"), {"TCS": candles()})
        (self.d("old") / "historical.json").write_text("{broken")
        make_set(self.d("new"), {"TCS": candles()})
        self.has(H.verify(self.d("new"), self.d("old")), "saved copy")


class Install(Tmp):
    def test_install_writes_the_files_and_reports_the_count(self):
        make_set(self.d("new"), {"TCS": candles()}, bench=candles(base=500))
        self.assertEqual(H.install(self.d("new"), self.d("old")), 2)
        self.assertEqual(H.verify(self.d("old"), None), [])
        self.assertEqual((self.d("old") / "shards" / "TCS.json").read_bytes(), (self.d("new") / "shards" / "TCS.json").read_bytes())
        self.assertEqual(H.install(self.d("new"), self.d("old")), 0)                   # unchanged files are not rewritten

    def test_install_never_deletes_anything_and_refuses_a_set_that_would_drop_a_saved_file(self):
        make_set(self.d("old"), {"TCS": candles(60), "INFY": candles(60)})
        extra = self.d("old") / "shards" / "WIPRO.json"
        extra.write_text(json.dumps(dict(META, stocks={"WIPRO": entry("WIPRO", candles())})))
        before = {p.name: p.read_bytes() for p in self.d("old").rglob("*") if p.is_file()}
        make_set(self.d("new"), {"TCS": candles(70), "INFY": candles(60)})              # WIPRO is not in the new set
        with self.assertRaises(H.HandoffError):
            H.install(self.d("new"), self.d("old"))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.d("old").rglob("*") if p.is_file()})
        self.assertTrue(extra.exists())
        make_set(self.d("new2"), {"TCS": candles(70), "INFY": candles(60), "WIPRO": candles()})
        H.install(self.d("new2"), self.d("old"))
        self.assertTrue(extra.exists())
        self.assertEqual(len(json.loads((self.d("old") / "shards" / "TCS.json").read_text())["stocks"]["TCS"]["candles"]), 70)
        src = (ROOT / "historical_handoff.py").read_text()
        self.assertNotRegex(src, r"unlink|rmtree\(old|\.remove\(|os\.remove|rmdir")                # (stage may remove its own half-built hand-over folder, never the saved copy)

    def test_install_refuses_and_writes_nothing_when_verification_fails(self):
        make_set(self.d("old"), {"TCS": candles(60)})
        snap = {p.name: p.read_bytes() for p in self.d("old").rglob("*") if p.is_file()}
        cs = candles(60)
        del cs[10]
        make_set(self.d("new"), {"TCS": cs})
        with self.assertRaises(H.HandoffError):
            H.install(self.d("new"), self.d("old"))
        self.assertEqual(snap, {p.name: p.read_bytes() for p in self.d("old").rglob("*") if p.is_file()})

    def test_install_writes_only_under_the_target(self):
        make_set(self.d("new"), {"TCS": candles()})
        H.install(self.d("new"), self.d("old") / "historical")
        written = {str(p.relative_to(self.t)) for p in self.t.rglob("*") if p.is_file() and "new" not in p.relative_to(self.t).parts}
        self.assertTrue(all(w.startswith("old/historical/") for w in written), written)


class Seed(Tmp):
    def test_seed_without_a_saved_copy_is_a_no_op(self):
        self.assertEqual(H.seed(self.d("nothing"), self.d("out")), 0)
        self.assertFalse(self.d("out").exists())

    def test_seed_merges_by_date_and_never_drops_a_day(self):
        make_set(self.d("saved"), {"TCS": candles(60, "2026-06-01")}, bench=candles(60, "2026-06-01", 500))
        out = self.d("out")
        (out / "by_symbol" / "historical").mkdir(parents=True)
        newer = candles(30, "2026-07-15")                                            # cache holds later days, not the early ones
        (out / "by_symbol" / "historical" / "TCS.json").write_text(json.dumps(dict(META, stocks={"TCS": entry("TCS", newer)})))
        H.seed(self.d("saved"), out)
        merged = json.loads((out / "by_symbol" / "historical" / "TCS.json").read_text())["stocks"]["TCS"]["candles"]
        dates = [c["date"] for c in merged]
        self.assertEqual(dates, sorted(set(dates)))
        self.assertTrue({c["date"] for c in candles(60, "2026-06-01")} | {c["date"] for c in newer} <= set(dates))
        main = json.loads((out / "historical.json").read_text())
        self.assertIn("TCS", main["stocks"])
        self.assertEqual(len(main["benchmark"]["candles"]), 60)

    def test_the_saved_copy_wins_on_a_day_both_have(self):
        make_set(self.d("saved"), {"TCS": candles(10)})
        out = self.d("out")
        (out / "by_symbol" / "historical").mkdir(parents=True)
        other = candles(10)
        other[2]["close"] = 999.0
        (out / "by_symbol" / "historical" / "TCS.json").write_text(json.dumps(dict(META, stocks={"TCS": entry("TCS", other)})))
        H.seed(self.d("saved"), out)
        got = json.loads((out / "by_symbol" / "historical" / "TCS.json").read_text())["stocks"]["TCS"]["candles"]
        self.assertEqual(got[2]["close"], candles(10)[2]["close"])

    def test_a_damaged_saved_copy_seeds_nothing(self):
        make_set(self.d("saved"), {"TCS": candles()})
        (self.d("saved") / "historical.json").write_text("{broken")
        with self.assertRaises(H.HandoffError):
            H.seed(self.d("saved"), self.d("out"))
        self.assertFalse(self.d("out").exists())

    def test_a_damaged_cache_file_is_replaced_by_the_saved_one(self):
        make_set(self.d("saved"), {"TCS": candles()})
        out = self.d("out")
        (out / "by_symbol" / "historical").mkdir(parents=True)
        (out / "by_symbol" / "historical" / "TCS.json").write_text("{broken")
        H.seed(self.d("saved"), out)
        self.assertEqual(len(json.loads((out / "by_symbol" / "historical" / "TCS.json").read_text())["stocks"]["TCS"]["candles"]), 60)


class Stage(Tmp):
    def build_out(self, out, stocks, bench=None):
        make_set(self.d("tmp_" + out.name), stocks, bench=bench)
        src = self.d("tmp_" + out.name)
        out.mkdir(parents=True, exist_ok=True)
        (out / "historical.json").write_bytes((src / "historical.json").read_bytes())
        (out / "by_symbol" / "historical").mkdir(parents=True, exist_ok=True)
        for f in (src / "shards").glob("*.json"):
            (out / "by_symbol" / "historical" / f.name).write_bytes(f.read_bytes())

    def test_stage_copies_a_verified_set(self):
        self.build_out(self.d("out"), {"TCS": candles()}, bench=candles(base=500))
        self.assertEqual(H.stage(self.d("out"), self.d("hand")), 1)
        self.assertEqual(sorted(p.name for p in self.d("hand").rglob("*") if p.is_file()), ["TCS.json", "historical.json"])

    def test_stage_refuses_a_missing_file_a_bad_file_and_a_used_folder(self):
        self.d("out").mkdir()
        with self.assertRaises(H.HandoffError):
            H.stage(self.d("out"), self.d("hand"))
        self.build_out(self.d("out2"), {"TCS": candles()})
        (self.d("out2") / "by_symbol" / "historical" / "TCS.json").write_text("{broken")
        with self.assertRaises(H.HandoffError):
            H.stage(self.d("out2"), self.d("hand2"))
        self.assertFalse(self.d("hand2").exists())                                   # nothing is left behind to be uploaded
        self.build_out(self.d("out3"), {"TCS": candles()})
        self.d("hand3").mkdir()
        (self.d("hand3") / "x").write_text("x")
        with self.assertRaises(H.HandoffError):
            H.stage(self.d("out3"), self.d("hand3"))

    def test_stage_ignores_the_other_shard_kinds_and_the_index(self):
        self.build_out(self.d("out"), {"TCS": candles()})
        (self.d("out") / "by_symbol" / "index.json").write_text("{}")
        (self.d("out") / "by_symbol" / "shareholding").mkdir()
        (self.d("out") / "by_symbol" / "shareholding" / "TCS.json").write_text("{}")
        H.stage(self.d("out"), self.d("hand"))
        self.assertEqual(sorted(p.name for p in self.d("hand").rglob("*") if p.is_file()), ["TCS.json", "historical.json"])


class EndToEnd(Tmp):
    def test_the_whole_flow_with_the_real_shard_writer(self):
        # a run with no saved copy, then a later run that extends it
        out = self.d("out1")
        for sym, cs in (("TCS", candles(60)),):
            shards.write_shard(out.parent / "w1", "historical", sym, META, entry(sym, cs))
        work = self.d("w1")
        (work / "out").mkdir(exist_ok=True)
        main = dict(META, benchmark={"symbol": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50", "candles": candles(60, base=500)}, stocks={"TCS": entry("TCS", candles(60))}, errors=[])
        (work / "out" / "historical.json").write_text(json.dumps(main))
        H.stage(work / "out", self.d("hand1"))
        H.install(self.d("hand1"), self.d("branch") / "historical")
        # a second, cache-less run (fresh out/), seeded from the saved copy, then extended by 5 days
        work2 = self.d("w2")
        H.seed(self.d("branch") / "historical", work2 / "out")
        sh = json.loads((work2 / "out" / "by_symbol" / "historical" / "TCS.json").read_text())
        sh["stocks"]["TCS"]["candles"] = candles(65)
        (work2 / "out" / "by_symbol" / "historical" / "TCS.json").write_text(json.dumps(sh))
        m = json.loads((work2 / "out" / "historical.json").read_text())
        m["stocks"]["TCS"]["candles"] = candles(65)
        (work2 / "out" / "historical.json").write_text(json.dumps(m))
        H.stage(work2 / "out", self.d("hand2"))
        self.assertEqual(H.verify(self.d("hand2"), self.d("branch") / "historical"), [])
        H.install(self.d("hand2"), self.d("branch") / "historical")
        final = json.loads((self.d("branch") / "historical" / "shards" / "TCS.json").read_text())["stocks"]["TCS"]["candles"]
        self.assertEqual(len(final), 65)

    def test_without_seeding_a_cache_less_run_would_be_refused_not_saved(self):
        make_set(self.d("saved"), {"TCS": candles(60), "INFY": candles(60)})
        fresh = self.d("fresh")
        make_set(fresh, {"TCS": candles(70)})                                        # a TCS-only run that never saw INFY
        self.assertNotEqual(H.verify(fresh, self.d("saved")), [])

    def test_the_updater_candle_shape_is_the_checked_shape(self):
        try:
            import historical_updater as U
        except ImportError:
            self.skipTest("requests is not installed here")
        c = U.clean_candle(["2026-10-01T00:00:00+05:30", 100, 105, 99, 103, 12345, 0])
        self.assertEqual(set(c), H.CANDLE_KEYS)
        self.assertEqual(H._candle_problems([c], "x"), [])


class Cli(Tmp):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        rc = H.main(list(argv), out, err)
        return rc, out.getvalue(), err.getvalue()

    def test_usage_and_exit_codes(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("nope", "a", "b")[0], 2)
        self.assertEqual(self.run_cli("verify", "a")[0], 2)
        make_set(self.d("new"), {"TCS": candles()})
        self.assertEqual(self.run_cli("verify", str(self.d("new")), str(self.d("old")))[0], 0)
        self.assertEqual(self.run_cli("install", str(self.d("new")), str(self.d("old")))[0], 0)
        cs = candles()
        del cs[5]
        make_set(self.d("bad"), {"TCS": cs})
        rc, out, err = self.run_cli("verify", str(self.d("bad")), str(self.d("old")))
        self.assertEqual(rc, 1)
        self.assertIn("::error::", err)

    def test_it_never_prints_file_contents(self):
        make_set(self.d("new"), {"TCS": candles()})
        doc = json.loads((self.d("new") / "historical.json").read_text())
        doc["errors"] = [{"symbol": "TCS", "error": "Bearer SECRETVALUE123"}]
        (self.d("new") / "historical.json").write_text(json.dumps(doc))
        rc, out, err = self.run_cli("verify", str(self.d("new")), str(self.d("old")))
        self.assertEqual(rc, 1)
        self.assertNotIn("SECRETVALUE123", out + err)


def block(text, job):
    m = re.search(r"^  %s:\n(.*?)(?=^  \S[\w-]*:\n|\Z)" % re.escape(job), text, re.M | re.S)
    assert m, job
    return m.group(1)


class WorkflowWiring(unittest.TestCase):
    H = (WF / "historical.yml").read_text()
    S = (WF / "save_historical.yml").read_text()
    U = (WF / "update.yml").read_text()

    def test_the_first_test_stays_tcs_only(self):
        self.assertIn('HISTORICAL_SYMBOLS: "${{ github.event_name == \'push\' && \'TCS\' || inputs.symbols || \'TCS\' }}"', self.H)
        self.assertRegex(self.H, r'symbols:\n(?:.*\n)*?\s+default: "TCS"')
        self.assertNotIn("universe", block(self.H, "historical").split("Update historical prices")[1].split("run:")[0])

    def test_a_push_never_saves_or_uploads(self):
        for name in ("Check the result and prepare the hand-over", "Hand the result to the save job"):
            seg = self.H.split("- name: " + name)[1].split("- name:")[0].split("\n  save-historical")[0]
            self.assertIn("if: github.event_name == 'workflow_dispatch'", seg, name)
        save = block(self.H, "save-historical")
        self.assertIn("if: ${{ github.event_name == 'workflow_dispatch' }}", save)
        self.assertIn("needs: historical", save)

    def test_only_the_save_job_can_write_and_it_holds_no_secret(self):
        self.assertEqual(self.H.count("contents: write"), 1)
        save = block(self.H, "save-historical")
        self.assertIn("contents: write", save)
        self.assertNotIn("UPSTOX", save)
        self.assertNotIn("secrets", save)
        self.assertNotIn("contents: write", block(self.H, "historical"))
        self.assertEqual(self.H.count("UPSTOX_ANALYTICS_TOKEN"), 2)             # the one use in the update step: the name and its secret
        upd = self.H.split("- name: Update historical prices")[1].split("- name:")[0]
        self.assertIn("UPSTOX_ANALYTICS_TOKEN: ${{ secrets.UPSTOX_ANALYTICS_TOKEN }}", upd)
        for after in ("Check the result and prepare the hand-over", "Hand the result to the save job"):
            seg = self.H.split("- name: " + after)[1].split("- name:")[0]
            self.assertNotIn("UPSTOX", seg)
            self.assertNotIn("secrets", seg)

    def test_the_order_is_seed_then_update_then_stage_then_upload(self):
        order = [self.H.index(x) for x in ("historical_handoff.py seed ledger-branch/historical out", "python historical_updater.py", "historical_handoff.py stage out handoff", "upload-artifact@v4")]
        self.assertEqual(order, sorted(order))
        self.assertIn("name: historical-handoff", self.H)
        self.assertIn("if-no-files-found: error", self.H)

    def test_the_save_workflow_writes_only_under_historical(self):
        s = self.S
        self.assertEqual(s.count("git add"), 1)
        self.assertIn("git add -- historical", s)
        self.assertNotRegex(s, r"git add (-A|--all|\.)")
        self.assertIn("grep -v '^historical/'", s)
        self.assertRegex(s, r"grep -v -E '\^\[AM\]")
        self.assertIn("git push origin HEAD:stocklens-data", s)
        self.assertNotRegex(s, r"--force|git push -f|--force-with-lease|checkout -b|--orphan|git init|git rm|git mv|rm -")
        self.assertNotIn("create", re.sub(r'"[^"\n]*"', "", s.split("jobs:")[1]).lower().replace("created here", ""))
        order = [s.index(x) for x in ("historical_handoff.py verify handoff-new ledger-branch/historical", "historical_handoff.py install handoff-new ledger-branch/historical", "git commit -m", "git push origin")]
        self.assertEqual(order, sorted(order))

    def test_the_save_workflow_needs_the_branch_and_holds_no_secret(self):
        s = self.S
        self.assertIn("The stocklens-data branch does not exist. It is never created here", s)
        self.assertNotIn("UPSTOX", s)
        self.assertNotIn("secrets", s.lower())
        self.assertEqual(s.count("contents: write"), 1)
        self.assertIn("workflow_call", s)
        self.assertNotIn("\nconcurrency:", s)                                       # set on the job, so it cannot deadlock against the caller's workflow group
        self.assertIn("group: stocklens-ledger", block(s, "save"))
        self.assertIn("cancel-in-progress: false", block(s, "save"))
        self.assertIn("persist-credentials: false", s.split("Download the hand-over files")[0])

    def test_update_workflow_publishes_the_saved_files_with_guarded_copies_only(self):
        build = self.U.split("- name: Build site folder")[1].split("- name: Upload Pages artifact")[0]
        lines = [l.strip() for l in build.splitlines() if "historical" in l and not l.strip().startswith("#")]
        self.assertEqual(lines, [
            "if [ -f ledger-branch/historical/historical.json ]; then cp ledger-branch/historical/historical.json _site/out/historical.json; fi",
            "if ls ledger-branch/historical/shards/*.json >/dev/null 2>&1; then mkdir -p _site/out/by_symbol/historical; cp ledger-branch/historical/shards/*.json _site/out/by_symbol/historical/; fi"])
        self.assertLess(build.index("cp -r out/by_symbol _site/out/by_symbol"), build.index("ledger-branch/historical/historical.json"))
        self.assertEqual(self.U.count("historical_handoff"), 0)                      # update.yml runs no handoff code at all

    def test_nothing_else_changed(self):
        # the updaters, the shard writer, the ledgers' scripts and the other workflows are exactly as committed
        files = ["historical_updater.py", "shards.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py",
                 "nse_updater.py", "universe.py", "upstox_common.py", "ledger_storage.py", "shareholding_ledger.py", "stock_directory.py", "sector_publish.py", "sector_master.py",
                 ".github/workflows/save_ledger.yml", ".github/workflows/financial_history.yml", ".github/workflows/shareholding.yml", ".github/workflows/universe.yml",
                 ".github/workflows/sector_data.yml", ".github/workflows/market_data.yml"]
        r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--"] + files, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(r.stdout.split(), [])

    def test_the_sector_gate_is_untouched(self):
        for p in ROOT.glob("*.py"):
            if p.name.startswith(("sector_", "fundamentals_updater")):
                self.assertNotIn("historical_handoff", p.read_text(), p.name)
        self.assertNotIn("SECTOR_PUBLIC_DISPLAY_APPROVED", self.S + self.H + (ROOT / "historical_handoff.py").read_text())

    def test_the_handoff_module_cannot_reach_the_network_or_the_environment(self):
        code = re.sub(r'""".*?"""', "", (ROOT / "historical_handoff.py").read_text(), flags=re.S)
        for needle in ("requests", "urllib", "socket", "http", "environ", "getenv", "subprocess", "import os"):
            self.assertNotIn(needle, code, needle)


if __name__ == "__main__":
    unittest.main()
