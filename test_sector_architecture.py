"""
Tests for Sector Architecture Phase 1: sector_master, sector_classify, sector_provider, sector_master_updater, sector_data_updater,
validate_sector_outputs and sector_publish.
Run:  python3 -m unittest test_sector_architecture -v     (no network; every file is built in a temporary folder)

All symbols, ISINs and sector names below are made up for the test world, except the one real case the task names (see ClassifyTests).
"""
import ast
import copy
import csv
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import market_data_updater as mdu
import market_derive as md
import sector_classify as sc
import sector_data_updater as sdu
import sector_master as sm
import sector_master_updater as smu
import sector_provider as sp
import sector_publish as pub
import validate_sector_outputs as vso

HERE = Path(__file__).resolve().parent
IST = mdu.IST
NOW = dt.datetime(2026, 10, 6, 9, 0, tzinfo=IST)            # Tuesday; expected latest trading day = Monday 2026-10-05
TODAY = "2026-10-06"
SMALL_GATE = dict(mdu.GATE, min_universe=10, min_eq_rows_per_file=10)
HEADER = ["SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY",
          "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"]
SECTOR_MODULES = ["sector_master.py", "sector_classify.py", "sector_provider.py", "sector_master_updater.py", "sector_data_updater.py",
                  "validate_sector_outputs.py", "sector_publish.py"]


def isin(i):
    return "INE%08d1" % i


def inst(i, sector, symbol=None, cls="operating_equity", src="provider_sector_present", today="2026-09-20", status=None):
    label = sector
    return {"isin": isin(i), "symbol": symbol or "SYM%d" % i, "symbol_history": [], "company_name": "Company %d" % i, "exchange": "NSE",
            "instrument_class": cls, "class_source": src, "sector_source_label": label, "sector_key": sm.sector_key(label),
            "sector_group": sm.sector_key(label), "provider": "testprov" if label else None, "fetched_on": today, "first_seen": today,
            "last_confirmed": today, "status": status or sm.status_for(cls, label), "revisions": []}


def row(sym, prev, close, vol=1000, turnover=500.0):
    return {"symbol": sym, "date": "2026-10-05", "prev_close": prev, "open": None, "high": None, "low": None, "close": close,
            "volume": vol, "turnover": turnover, "deliv": None}


def world(n_per=6, sectors=("Alpha Sector", "Bravo Sector", "Charlie Sector"), extra_etf=2, extra_unclassified=1, extra_missing=1):
    """records + day rows. Sector s, stock j has change (j - 2)% so each sector has a known median."""
    recs, rows, i = {}, [], 0
    for k, s in enumerate(sectors):
        for j in range(n_per):
            i += 1
            r = inst(i, s)
            recs[r["isin"]] = r
            rows.append(row(r["symbol"], 100.0, 100.0 + (j - 2) + k * 0.5))
    for _ in range(extra_etf):
        i += 1
        r = inst(i, None, cls="etf", src="etf_list:test")
        recs[r["isin"]] = r
        rows.append(row(r["symbol"], 100.0, 101.0))
    for _ in range(extra_unclassified):
        i += 1
        r = inst(i, None, cls="unclassified", src="no_evidence")
        recs[r["isin"]] = r
        rows.append(row(r["symbol"], 100.0, 99.0))
    for _ in range(extra_missing):
        i += 1
        r = inst(i, None)
        recs[r["isin"]] = r
        rows.append(row(r["symbol"], 100.0, 100.5))
    return recs, rows


def write_bhav(root, rows_by_date):
    raw = Path(root) / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for d, rows in rows_by_date.items():
        with open(raw / ("bhav_%s.csv" % d.replace("-", "")), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            iso = dt.date.fromisoformat(d).strftime("%d-%b-%Y")
            for r in rows:
                c = r["close"]
                w.writerow([r["symbol"], "EQ", iso, r["prev_close"], c, c, c, c, c, c, r["volume"], r["turnover"], 10, 1, 40])


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ---------------------------------------------------------------- 1. master
class MasterTests(Tmp):
    def obs(self, i, sector="Alpha Sector", symbol=None, **kw):
        o = {"isin": isin(i), "symbol": symbol or "SYM%d" % i, "company_name": "Company %d" % i, "instrument_class": "operating_equity",
             "class_source": "provider_sector_present", "sector_source_label": sector, "provider_answered": True, "provider": "testprov", "fetched_on": TODAY}
        o.update(kw)
        return o

    def test_isin_uniqueness_is_enforced(self):
        a = inst(1, "Alpha Sector")
        b = dict(inst(2, "Alpha Sector"), isin=a["isin"])
        self.assertTrue(any("duplicate ISIN" in p for p in sm.validate_records([a, b])))

    def test_merge_ignores_duplicate_and_invalid_isin_observations(self):
        new, skipped = sm.merge({}, [self.obs(1), self.obs(1, symbol="OTHER"), self.obs(2, symbol="X") | {"isin": "BAD"}], TODAY)
        self.assertEqual(sorted(new), [isin(1)])
        self.assertEqual(new[isin(1)]["symbol"], "SYM1")
        self.assertEqual(skipped, 2)

    def test_symbol_isin_consistency(self):
        a, b = inst(1, "Alpha Sector", symbol="SAME"), inst(2, "Alpha Sector", symbol="SAME")
        self.assertTrue(any("belongs to two ISINs" in p for p in sm.validate_records([a, b])))
        b["status"] = "absent"                       # an absent record may keep an old symbol
        self.assertEqual(sm.validate_records([a, b]), [])

    def test_symbol_change_is_recorded_not_lost(self):
        old, _ = sm.merge({}, [self.obs(1, symbol="OLDNAME")], "2026-09-01")
        new, _ = sm.merge(old, [self.obs(1, symbol="NEWNAME")], TODAY)
        r = new[isin(1)]
        self.assertEqual((r["symbol"], r["symbol_history"]), ("NEWNAME", ["OLDNAME"]))
        self.assertEqual(r["revisions"][-1]["field"], "symbol")
        self.assertEqual(r["first_seen"], "2026-09-01")
        self.assertEqual(sm.validate_records(list(new.values())), [])

    def test_sector_label_is_preserved_exactly(self):
        label = "Consumer  Durables & Appliances"
        new, _ = sm.merge({}, [self.obs(1, sector=label)], TODAY)
        r = new[isin(1)]
        self.assertEqual(r["sector_source_label"], "Consumer Durables & Appliances")      # only whitespace is normalised, wording untouched
        self.assertEqual(r["sector_key"], "consumer_durables_appliances")
        self.assertEqual(r["provider"], "testprov")

    def test_label_survives_a_call_that_gave_no_answer(self):
        old, _ = sm.merge({}, [self.obs(1)], "2026-09-01")
        new, _ = sm.merge(old, [self.obs(1, sector=None, provider_answered=False, attempted=True, fetched_on=None, provider=None)], TODAY)
        r = new[isin(1)]
        self.assertEqual((r["sector_source_label"], r["status"], r["last_confirmed"]), ("Alpha Sector", "unconfirmed", "2026-09-01"))

    def test_an_answered_empty_sector_clears_the_label_and_records_it(self):
        old, _ = sm.merge({}, [self.obs(1)], "2026-09-01")
        new, _ = sm.merge(old, [self.obs(1, sector=None)], TODAY)
        r = new[isin(1)]
        self.assertEqual((r["sector_source_label"], r["sector_key"], r["status"]), (None, None, "missing_sector"))
        self.assertEqual(r["revisions"][-1]["old"], "Alpha Sector")

    def test_label_change_adds_a_revision(self):
        old, _ = sm.merge({}, [self.obs(1)], "2026-09-01")
        new, _ = sm.merge(old, [self.obs(1, sector="Bravo Sector")], TODAY)
        self.assertEqual([x["field"] for x in new[isin(1)]["revisions"]], ["sector_source_label"])

    def test_no_loss_records_are_kept_when_absent(self):
        old, _ = sm.merge({}, [self.obs(1), self.obs(2)], "2026-09-01")
        new, _ = sm.merge(old, [self.obs(1)], TODAY)
        self.assertEqual(sorted(new), [isin(1), isin(2)])
        self.assertEqual(new[isin(2)]["status"], "absent")
        self.assertEqual(sm.no_loss_problems(list(old.values()), list(new.values())), [])

    def test_no_loss_check_catches_vanished_and_collapsed_masters(self):
        old = [inst(i, "Alpha Sector") for i in range(1, 21)]
        self.assertTrue(any("vanished" in p for p in sm.no_loss_problems(old, old[:-1])))
        wiped = [dict(r, sector_source_label=None, sector_key=None, sector_group=None, status="missing_sector") for r in old]
        probs = sm.no_loss_problems(old, wiped)
        self.assertTrue(any("classified records fell" in p for p in probs))
        self.assertTrue(any("cleared at once" in p for p in probs))
        self.assertEqual(sm.no_loss_problems(old, copy.deepcopy(old)), [])

    def test_no_nan_or_infinity(self):
        r = inst(1, "Alpha Sector")
        r["revisions"] = [{"on": TODAY, "field": "x", "old": float("nan"), "new": 1}]
        self.assertTrue(any("non-finite" in p for p in sm.validate_records([r])))
        with self.assertRaises(ValueError):
            sm.dumps({"x": float("inf")})
        p = self.tmp / "m.json"
        p.write_text('{"kind": "sector_master", "schema_version": 1, "records": [NaN]}')
        recs, probs = sm.load(p)
        self.assertEqual((recs, len(probs)), ({}, 1))

    def test_deterministic_output(self):
        obs = [self.obs(i, sector="S%d" % (i % 3)) for i in range(1, 30)]
        a = sm.dumps(sm.to_doc(sm.merge({}, obs, TODAY)[0], TODAY))
        b = sm.dumps(sm.to_doc(sm.merge({}, list(reversed(obs)), TODAY)[0], TODAY))
        self.assertEqual(a, b)

    def test_load_missing_damaged_and_roundtrip(self):
        self.assertEqual(sm.load(self.tmp / "none.json"), ({}, []))
        bad = self.tmp / "bad.json"
        bad.write_text("{not json")
        self.assertEqual(sm.load(bad)[0], {})
        self.assertEqual(len(sm.load(bad)[1]), 1)                       # a damaged master is a problem, never "empty"
        recs, _ = sm.merge({}, [self.obs(1), self.obs(2)], TODAY)
        path = self.tmp / "private" / "sector_master.json"
        sm.write(path, recs, TODAY)
        back, probs = sm.load(path)
        self.assertEqual((back, probs), (recs, []))

    def test_validate_rejects_etf_with_sector_or_without_source(self):
        e = inst(1, None, cls="etf", src="etf_list:test")
        self.assertEqual(sm.validate_records([e]), [])
        self.assertTrue(any("without an authoritative" in p for p in sm.validate_records([dict(e, class_source=None)])))
        self.assertTrue(any("must not carry a sector" in p for p in sm.validate_records([dict(e, sector_source_label="X", sector_key="x", provider="p")])))


# ---------------------------------------------------------------- 2. classification
class ClassifyTests(Tmp):
    EV = {"source": "test list", "as_of": "2026-10-01", "exhaustive": True, "symbols": {"FUNDONE", "FUNDTWO"}}

    def test_etf_only_from_the_authoritative_list(self):
        self.assertEqual(sc.classify("FUNDONE", None, self.EV), ("etf", "etf_list:test list"))
        # a name or symbol that merely LOOKS like an ETF is not one
        for sym in ("GOLDBEES", "NIFTYETF", "BANKBEES", "FUNDTHREE", "MYETF"):
            self.assertNotEqual(sc.classify(sym, None, self.EV)[0], "etf", sym)
            self.assertNotEqual(sc.classify(sym, None, None)[0], "etf", sym)

    def test_without_an_etf_list_nothing_is_classified_as_etf_or_guessed(self):
        self.assertEqual(sc.classify("FUNDONE", None, None), ("unclassified", "no_evidence"))
        self.assertEqual(sc.classify("ANYTHING", None, None)[0], "unclassified")

    def test_non_exhaustive_list_does_not_prove_operating_equity(self):
        ev = dict(self.EV, exhaustive=False)
        self.assertEqual(sc.classify("OTHER", None, ev)[0], "unclassified")
        self.assertEqual(sc.classify("FUNDONE", None, ev)[0], "etf")

    def test_provider_label_proves_operating_equity(self):
        self.assertEqual(sc.classify("ABC", "Some Sector", None), ("operating_equity", "provider_sector_present"))

    def test_conflicting_evidence_stays_unclassified(self):
        self.assertEqual(sc.classify("FUNDONE", "Some Sector", self.EV), ("unclassified", "conflicting_evidence"))

    def test_buildpro_stays_operating_equity_with_missing_sector(self):
        """The named real case: a Upstox EQ instrument with no sector that is not on the ETF list."""
        o = {"isin": "INE240J01029", "symbol": "BUILDPRO", "company_name": "Shankara Buildpro Limited"}
        obs = smu.build_observations([o], {}, self.EV, {"INE240J01029": None}, {"INE240J01029"}, "testprov", TODAY)
        rec = sm.merge({}, obs, TODAY)[0]["INE240J01029"]
        self.assertEqual((rec["instrument_class"], rec["status"], rec["sector_source_label"], rec["sector_key"]), ("operating_equity", "missing_sector", None, None))
        # with no ETF list at all it is not guessed to be anything
        obs = smu.build_observations([o], {}, None, {"INE240J01029": None}, {"INE240J01029"}, "testprov", TODAY)
        rec = sm.merge({}, obs, TODAY)[0]["INE240J01029"]
        self.assertEqual((rec["instrument_class"], rec["status"], rec["sector_key"]), ("unclassified", "unclassified", None))

    def test_a_sector_is_never_assigned_by_the_classifier(self):
        o = {"isin": isin(1), "symbol": "ABC", "company_name": "Real Estate Developers Ltd"}
        obs = smu.build_observations([o], {}, self.EV, {isin(1): None}, {isin(1)}, "testprov", TODAY)
        self.assertEqual(obs[0]["sector_source_label"], None)          # a name that sounds like a sector gives no sector

    def test_parse_etf_csv(self):
        text = "﻿Issuer Name,Name,Symbol,Underlying,Launch Date\nA,Fund One,FUNDONE,X,2020\nB,Fund Two, fundtwo ,Y,2021\n"
        ev = sc.parse_etf_csv(text, "list", "2026-10-01")
        self.assertEqual(ev["symbols"], ["FUNDONE", "FUNDTWO"])
        with self.assertRaises(ValueError):
            sc.parse_etf_csv("a,b\n1,2\n", "list", "2026-10-01")
        with self.assertRaises(ValueError):
            sc.parse_etf_csv("Symbol\n", "list", "2026-10-01")

    def test_evidence_file_loading(self):
        self.assertEqual(sc.load_etf_evidence(self.tmp / "none.json"), (None, []))
        p = self.tmp / "e.json"
        p.write_text(json.dumps({"source": "s", "exhaustive": True, "symbols": ["AAA"]}))
        ev, probs = sc.load_etf_evidence(p)
        self.assertEqual((ev["symbols"], probs), ({"AAA"}, []))
        p.write_text(json.dumps({"symbols": ["AAA"]}))                                  # no source = not authoritative
        self.assertEqual(sc.load_etf_evidence(p)[0], None)
        p.write_text("{")
        self.assertEqual(len(sc.load_etf_evidence(p)[1]), 1)


# ---------------------------------------------------------------- 3. provider + updater
class FakeProvider(sp.SectorProvider):
    name = "fakeprov"

    def __init__(self, items, fail=()):
        self.items, self.fail, self.asked = items, set(fail), []

    def universe(self):
        return [{k: v for k, v in x.items() if k != "sector"} for x in self.items]

    def sector_for(self, i):
        self.asked.append(i)
        if i in self.fail:
            return None, False
        return next(x for x in self.items if x["isin"] == i).get("sector"), True


def items(n=12, etfs=(), missing=()):
    out = []
    for i in range(1, n + 1):
        sym = "SYM%d" % i
        out.append({"isin": isin(i), "symbol": sym, "company_name": "Company %d" % i, "exchange": "NSE",
                    "sector": None if (i in missing or sym in etfs) else "Sector %d" % (i % 3)})
    return out


class UpdaterTests(Tmp):
    def etf_file(self, symbols, exhaustive=True):
        p = self.tmp / "private" / "etf_list.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"source": "test list", "as_of": "2026-10-01", "exhaustive": exhaustive, "symbols": symbols}))
        return p

    def test_end_to_end_master_build(self):
        prov = FakeProvider(items(12, etfs=("SYM11", "SYM12"), missing=(5,)))
        self.etf_file(["SYM11", "SYM12"])
        code, msgs, s = smu.run(prov, self.tmp, today=TODAY)
        self.assertEqual(code, 0, msgs)
        recs, probs = sm.load(self.tmp / "private" / "sector_master.json")
        self.assertEqual(probs, [])
        self.assertEqual(len(recs), 12)
        self.assertEqual({recs[isin(11)]["instrument_class"], recs[isin(12)]["instrument_class"]}, {"etf"})
        self.assertEqual((recs[isin(5)]["instrument_class"], recs[isin(5)]["status"]), ("operating_equity", "missing_sector"))
        self.assertEqual(s["status"], {"classified": 9, "excluded": 2, "missing_sector": 1})

    def test_console_summary_holds_only_aggregate_counts(self):
        prov = FakeProvider(items(12))
        code, msgs, _ = smu.run(prov, self.tmp, today=TODAY)
        text = " ".join(msgs)
        self.assertNotRegex(text, r"Sector \d|SYM\d|INE\d")
        self.assertEqual(code, 0)

    def test_second_run_is_stable_and_loses_nothing(self):
        prov = FakeProvider(items(12))
        smu.run(prov, self.tmp, today=TODAY)
        first = (self.tmp / "private" / "sector_master.json").read_text()
        prov2 = FakeProvider(items(12))
        code, _, _ = smu.run(prov2, self.tmp, today=TODAY)
        self.assertEqual(code, 0)
        self.assertEqual(prov2.asked, [])                                  # nothing was due: no provider call
        self.assertEqual((self.tmp / "private" / "sector_master.json").read_text(), first)       # deterministic, identical

    def test_plan_fetches_orders_never_fetched_first_then_oldest_and_respects_budget(self):
        old = {isin(1): inst(1, "A", today="2026-08-01"), isin(2): inst(2, "A", today="2026-07-01"), isin(3): inst(3, "A", today="2026-10-05")}
        uni = [{"isin": isin(i)} for i in (1, 2, 3, 4)]
        self.assertEqual(smu.plan_fetches(uni, old, TODAY, 30, 10), [isin(4), isin(2), isin(1)])
        self.assertEqual(smu.plan_fetches(uni, old, TODAY, 30, 2), [isin(4), isin(2)])
        self.assertEqual(smu.plan_fetches(uni, old, TODAY, 30, 0), [])

    def test_provider_errors_keep_the_old_label(self):
        smu.run(FakeProvider(items(12)), self.tmp, today="2026-08-01")
        prov = FakeProvider(items(12), fail={isin(1)})
        code, _, _ = smu.run(prov, self.tmp, today=TODAY)
        self.assertEqual(code, 0)
        r = sm.load(self.tmp / "private" / "sector_master.json")[0][isin(1)]
        self.assertEqual((r["sector_source_label"], r["status"], r["last_confirmed"]), ("Sector 1", "unconfirmed", "2026-08-01"))

    def test_refuses_a_collapsed_universe_and_a_damaged_master(self):
        smu.run(FakeProvider(items(12)), self.tmp, today=TODAY)
        before = (self.tmp / "private" / "sector_master.json").read_text()
        code, msgs, _ = smu.run(FakeProvider(items(3)), self.tmp, today="2026-12-01")
        self.assertEqual(code, 1)
        self.assertEqual((self.tmp / "private" / "sector_master.json").read_text(), before)
        (self.tmp / "private" / "sector_master.json").write_text("garbage")
        code, msgs, _ = smu.run(FakeProvider(items(12)), self.tmp, today=TODAY)
        self.assertEqual(code, 1)
        self.assertEqual((self.tmp / "private" / "sector_master.json").read_text(), "garbage")      # never overwritten blindly

    def test_refuses_duplicate_isins_in_the_provider_universe(self):
        its = items(12)
        its[1]["isin"] = its[0]["isin"]
        self.assertEqual(smu.run(FakeProvider(its), self.tmp, today=TODAY)[0], 1)
        self.assertFalse((self.tmp / "private" / "sector_master.json").exists())

    def test_file_provider(self):
        p = self.tmp / "prov.json"
        p.write_text(json.dumps({"provider": "licensed-x", "instruments": items(4)}))
        fp = sp.FileProvider(p)
        self.assertEqual((fp.name, len(fp.universe())), ("licensed-x", 4))
        self.assertEqual(fp.sector_for(isin(1)), ("Sector 1", True))
        self.assertEqual(fp.sector_for("INE999999999"), (None, False))
        code, _, _ = smu.run(fp, self.tmp, today=TODAY)
        self.assertEqual(code, 0)
        self.assertEqual(sm.load(self.tmp / "private" / "sector_master.json")[0][isin(1)]["provider"], "licensed-x")

    def test_upstox_provider_uses_the_shared_client_and_reads_data_sector(self):
        class FakeClient:
            def __init__(self):
                self.paths = []

            def get(self, path):
                self.paths.append(path)
                return ({"status": "success", "data": {"sector": " Sector X "}}, None) if "INE00000001" in path else (None, "HTTP 500")

        c = FakeClient()
        up = sp.UpstoxProvider(client=c, instruments={"ABC": {"isin": isin(1), "name": "Abc"}})
        self.assertEqual(up.universe(), [{"isin": isin(1), "symbol": "ABC", "company_name": "Abc", "exchange": "NSE"}])
        self.assertEqual(up.sector_for(isin(1)), ("Sector X", True))
        self.assertEqual(up.sector_for(isin(2)), (None, False))
        self.assertEqual(c.paths[0], isin(1) + "/profile")

    def test_garbled_upstox_replies_never_count_as_no_sector(self):
        class C:
            def __init__(self, body):
                self.body = body

            def get(self, path):
                return self.body, None

        for body in ({"status": "success"}, {"status": "success", "data": None}, {"status": "success", "data": []}, {"data": "x"}, "text", None):
            up = sp.UpstoxProvider(client=C(body), instruments={})
            self.assertEqual(up.sector_for(isin(1)), (None, False), repr(body))
        up = sp.UpstoxProvider(client=C({"status": "success", "data": {"sector": None}}), instruments={})
        self.assertEqual(up.sector_for(isin(1)), (None, True))                  # an explicit empty sector is an answer

    def test_garbled_replies_cannot_erase_an_existing_master(self):
        smu.run(FakeProvider(items(12)), self.tmp, today="2026-08-01")
        before = sm.load(self.tmp / "private" / "sector_master.json")[0]

        class Garbled(FakeProvider):
            def sector_for(self, i):
                self.asked.append(i)
                return None, False

        code, _, _ = smu.run(Garbled(items(12)), self.tmp, today=TODAY)
        after = sm.load(self.tmp / "private" / "sector_master.json")[0]
        self.assertEqual(code, 0)
        self.assertEqual({k: v["sector_source_label"] for k, v in after.items()}, {k: v["sector_source_label"] for k, v in before.items()})

    def test_empty_provider_universe_cannot_overwrite_a_valid_master(self):
        smu.run(FakeProvider(items(12)), self.tmp, today=TODAY)
        before = (self.tmp / "private" / "sector_master.json").read_text()
        self.assertEqual(smu.run(FakeProvider([]), self.tmp, today="2026-12-01")[0], 1)
        self.assertEqual((self.tmp / "private" / "sector_master.json").read_text(), before)

    def test_first_build_is_announced(self):
        _, msgs, _ = smu.run(FakeProvider(items(12)), self.tmp, today=TODAY)
        self.assertTrue(any("no previous sector master" in m for m in msgs))
        _, msgs, _ = smu.run(FakeProvider(items(12)), self.tmp, today=TODAY)
        self.assertFalse(any("no previous sector master" in m for m in msgs))

    def test_etf_import_cli_writes_private_evidence_and_prints_counts_only(self):
        import io
        import contextlib
        csv_path = self.tmp / "etf.csv"
        csv_path.write_text("Issuer Name,Name,Symbol\nA,Fund One,FUNDONE\nB,Fund Two,FUNDTWO\n")
        out = self.tmp / "private" / "etf_list.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sc.main(["import-etf", "--csv", str(csv_path), "--source", "NSE ETF list", "--as-of", "2026-10-01", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertNotIn("FUNDONE", buf.getvalue())
        ev, probs = sc.load_etf_evidence(out)
        self.assertEqual((ev["symbols"], ev["exhaustive"], probs), ({"FUNDONE", "FUNDTWO"}, True, []))
        bad = self.tmp / "bad.csv"
        bad.write_text("a,b\n1,2\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sc.main(["import-etf", "--csv", str(bad), "--source", "s", "--as-of", "d", "--out", str(self.tmp / "x.json")]), 1)
        self.assertFalse((self.tmp / "x.json").exists())
        self.assertEqual(sc.main([]), 2)


# ---------------------------------------------------------------- 4. sector performance
class PerformanceTests(Tmp):
    def docs(self, recs=None, rows=None, now=NOW, **kw):
        if recs is None:
            recs, rows = world(**kw)
        dq = {"status": "pass", "source_is_latest_trading_day": True}
        return sdu.build_docs(rows, recs, "2026-10-05", now, dq)

    def test_aggregation_matches_known_numbers(self):
        d = self.docs()["sectors"]
        self.assertTrue(d["available"])
        self.assertEqual([s["key"] for s in d["sectors"]], ["alpha_sector", "bravo_sector", "charlie_sector"])
        s = d["sectors"][0]
        # changes are -2,-1,0,1,2,3 percent
        self.assertEqual((s["stock_count"], s["counted"], s["advances"], s["declines"], s["unchanged"]), (6, 6, 3, 2, 1))
        self.assertEqual((s["median_1d_pct"], s["mean_1d_pct"], s["breadth_pct"]), (0.5, 0.5, 50.0))
        self.assertEqual(s["volume"], 6000)
        self.assertTrue(s["eligible"])

    def test_uses_the_existing_market_functions(self):
        recs, rows = world()
        d = self.docs(recs, rows)["sectors"]
        g = [r for r in rows if recs[isin(int(r["symbol"][3:]))]["sector_key"] == "alpha_sector"]
        b = md.breadth(g)
        s = d["sectors"][0]
        self.assertEqual((s["advances"], s["declines"], s["unchanged"]), (b["advancing"], b["declining"], b["unchanged"]))

    def test_headline_is_equal_weight_median_not_cap_weighted(self):
        recs, rows = world(n_per=5, sectors=("Alpha Sector", "Bravo Sector", "Charlie Sector"))
        a = [r for r in rows if r["symbol"] in ("SYM1", "SYM2", "SYM3", "SYM4", "SYM5")]
        for r, c in zip(a, (101, 101, 101, 101, 150)):                     # one huge, high-turnover winner must not move the median
            r["close"], r["turnover"], r["volume"] = c, (1e9 if c == 150 else 500.0), 10 ** 9 if c == 150 else 1000
        s = self.docs(recs, rows)["sectors"]["sectors"][0]
        self.assertEqual(s["median_1d_pct"], 1.0)
        self.assertGreater(s["mean_1d_pct"], 9.0)
        self.assertIn("no market-cap weighting", self.docs()["sectors"]["headline_method"])

    def test_etf_unclassified_and_sectorless_rows_are_outside_every_sector(self):
        d = self.docs()["sectors"]
        rc = d["reconciliation"]
        self.assertEqual(rc, dict(rc, eq_rows=22, etf=2, unclassified=1, operating_missing_sector=1, in_sector=18, not_in_master=0))
        self.assertEqual(sum(s["stock_count"] for s in d["sectors"]), 18)

    def test_universe_definitions_differ_and_are_reconciled(self):
        """The breadth universe is every EQ row (ETFs included); the sector universe is not. The file measures the difference."""
        recs, rows = world()
        breadth = md.breadth(rows)["counted"]
        d = self.docs(recs, rows)["sectors"]
        self.assertEqual(breadth, d["reconciliation"]["eq_rows"])
        self.assertLess(d["reconciliation"]["in_sector"], breadth)
        self.assertIn("breadth universe", sdu.__doc__)
        self.assertIn("sector universe", sdu.__doc__)

    def test_rows_not_in_master_are_counted_not_dropped_silently(self):
        recs, rows = world()
        rows.append(row("NOTINMASTER", 100, 101))
        rc = self.docs(recs, rows)["sectors"]["reconciliation"]
        self.assertEqual((rc["not_in_master"], rc["eq_rows"]), (1, 23))

    def test_absent_master_records_do_not_match_rows(self):
        recs, rows = world()
        for r in recs.values():
            if r["sector_key"] == "alpha_sector":
                r["status"] = "absent"
        d = self.docs(recs, rows)["sectors"]
        self.assertNotIn("alpha_sector", [s["key"] for s in d["sectors"]])

    def test_insufficient_coverage_is_unavailable(self):
        recs, rows = world(n_per=3, sectors=("Alpha Sector", "Bravo Sector", "Charlie Sector"), extra_missing=30)
        d = self.docs(recs, rows)["sectors"]
        self.assertFalse(d["available"])
        self.assertEqual((d["sectors"], d["leaders"], d["laggards"]), ([], [], []))
        self.assertIn("coverage", d["unavailable_reason"])

    def test_small_sector_gets_no_headline(self):
        recs, rows = world(n_per=6)
        for i in range(100, 103):
            r = inst(i, "Tiny Sector")
            recs[r["isin"]] = r
            rows.append(row(r["symbol"], 100, 101))
        d = self.docs(recs, rows)["sectors"]
        t = next(s for s in d["sectors"] if s["key"] == "tiny_sector")
        self.assertEqual((t["eligible"], t["median_1d_pct"], t["mean_1d_pct"]), (False, None, None))
        self.assertNotIn("tiny_sector", [x["key"] for x in d["leaders"] + d["laggards"]])
        self.assertEqual(vso.validate(self.docs(recs, rows)), [])

    def test_too_few_eligible_sectors_is_unavailable(self):
        recs, rows = world(n_per=6, sectors=("Alpha Sector", "Bravo Sector"))
        d = self.docs(recs, rows)["sectors"]
        self.assertFalse(d["available"])

    def test_stale_master_is_unavailable(self):
        recs, rows = world()
        for r in recs.values():
            r["last_confirmed"] = "2026-06-01"
        d = self.docs(recs, rows)["sectors"]
        self.assertFalse(d["available"])
        self.assertIn("stale", d["unavailable_reason"])

    def test_freshness_tolerates_a_minority_of_old_labels(self):
        recs, rows = world(n_per=20)
        old = [r for r in recs.values() if r["sector_key"]][:3]
        for r in old:
            r["last_confirmed"] = "2026-06-01"
        self.assertTrue(self.docs(recs, rows)["sectors"]["available"])

    def test_missing_or_empty_master_falls_back_to_unavailable(self):
        recs, rows = world()
        d = self.docs({}, rows)["sectors"]
        self.assertEqual((d["available"], d["sectors"]), (False, []))
        self.assertEqual(vso.validate(self.docs({}, rows)), [])

    def test_leaders_and_laggards_are_ranked_and_capped(self):
        recs, rows = world(n_per=6, sectors=tuple("Sector %s" % c for c in "ABCDEFGH"))
        for r in rows:
            n = int(r["symbol"][3:])
            if n <= 48:
                r["close"] = 100 + ((n - 1) // 6) * 0.5 + ((n - 1) % 6)
        d = self.docs(recs, rows)["sectors"]
        lead = [x["median_1d_pct"] for x in d["leaders"]]
        lag = [x["median_1d_pct"] for x in d["laggards"]]
        self.assertEqual((len(lead), len(lag)), (5, 5))
        self.assertEqual(lead, sorted(lead, reverse=True))
        self.assertEqual(lag, sorted(lag))

    def test_outputs_are_finite_deterministic_and_valid(self):
        recs, rows = world()
        rows.append(row("ODD", None, 100))                                  # no previous close: no change, must not become NaN
        a = self.docs(recs, rows)
        b = self.docs(copy.deepcopy(recs), list(rows))
        self.assertEqual(mdu.dumps(a["sectors"]), mdu.dumps(b["sectors"]))
        self.assertEqual(mdu.dumps(a["stocks"]), mdu.dumps(b["stocks"]))
        text = mdu.dumps(a["sectors"]) + mdu.dumps(a["stocks"])
        self.assertNotRegex(text, r"NaN|Infinity")
        self.assertEqual(vso.validate(a), [])

    def test_validator_catches_tampering(self):
        base = self.docs()
        for name, mut in [
            ("advances do not add up", lambda d: d["sectors"]["sectors"][0].update(advances=99)),
            ("nan", lambda d: d["sectors"]["sectors"][0].update(median_1d_pct=float("nan"))),
            ("unavailable with rows", lambda d: d["sectors"].update(available=False, unavailable_reason="x")),
            ("bad approval flag", lambda d: d["sectors"].update(public_display_approved="yes")),
            ("wrong kind", lambda d: d["stocks"].update(kind="sectors")),
            ("leaders out of order", lambda d: d["sectors"].update(leaders=list(reversed(d["sectors"]["leaders"])))),
            ("a symbol in two sectors", lambda d: d["stocks"]["sectors"]["bravo_sector"]["members"].append(d["stocks"]["sectors"]["alpha_sector"]["members"][0])),
            ("a live word", lambda d: d["sectors"].update(headline_method="live data")),
        ]:
            d = copy.deepcopy(base)
            mut(d)
            self.assertTrue(vso.validate(d), name)

    def test_full_run_on_disk_writes_private_files_only(self):
        recs, rows = world(n_per=8)
        write_bhav(self.tmp, {"2026-10-02": [dict(r, close=r["prev_close"]) for r in rows], "2026-10-05": rows})
        sm.write(self.tmp / "private" / "sector_master.json", recs, "2026-10-01")
        code, msgs = sdu.run(self.tmp, NOW, gate=SMALL_GATE)
        self.assertEqual(code, 0, msgs)
        self.assertTrue((self.tmp / "private" / "market_sectors.json").exists())
        self.assertTrue((self.tmp / "private" / "market_sector_stocks.json").exists())
        self.assertFalse((self.tmp / "out").exists(), "nothing may be written to out/")
        doc = json.loads((self.tmp / "private" / "market_sectors.json").read_text())
        self.assertEqual((doc["available"], doc["public_display_approved"], doc["freshness"]), (True, False, "EOD"))
        self.assertEqual(vso.main(["--dir", str(self.tmp / "private")]), 0)

    def test_full_run_without_a_master_writes_an_unavailable_file(self):
        recs, rows = world(n_per=8)
        write_bhav(self.tmp, {"2026-10-02": [dict(r, close=r["prev_close"]) for r in rows], "2026-10-05": rows})
        code, _ = sdu.run(self.tmp, NOW, gate=SMALL_GATE)
        self.assertEqual(code, 0)
        doc = json.loads((self.tmp / "private" / "market_sectors.json").read_text())
        self.assertEqual((doc["available"], doc["sectors"]), (False, []))

    def test_full_run_with_damaged_master_is_unavailable_not_a_crash(self):
        recs, rows = world(n_per=8)
        write_bhav(self.tmp, {"2026-10-02": [dict(r, close=r["prev_close"]) for r in rows], "2026-10-05": rows})
        (self.tmp / "private").mkdir()
        (self.tmp / "private" / "sector_master.json").write_text("{bad")
        self.assertEqual(sdu.run(self.tmp, NOW, gate=SMALL_GATE)[0], 0)
        self.assertFalse(json.loads((self.tmp / "private" / "market_sectors.json").read_text())["available"])

    def test_market_gate_failure_writes_nothing(self):
        code, msgs = sdu.run(self.tmp, NOW, gate=SMALL_GATE)
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "private").exists())

    def test_existing_market_pipeline_still_counts_etfs_in_breadth(self):
        """Documents the answer to the ETF/breadth question: clean_day filters on SERIES == EQ only."""
        raw = [{"SYMBOL": "AAA", "SERIES": "EQ", "DATE1": "05-Oct-2026", "PREV_CLOSE": "100", "OPEN_PRICE": "100", "HIGH_PRICE": "101", "LOW_PRICE": "99",
                "CLOSE_PRICE": "101", "TTL_TRD_QNTY": "10", "TURNOVER_LACS": "5", "DELIV_PER": "50"},
               {"SYMBOL": "FUNDETF", "SERIES": "EQ", "DATE1": "05-Oct-2026", "PREV_CLOSE": "100", "OPEN_PRICE": "100", "HIGH_PRICE": "101", "LOW_PRICE": "99",
                "CLOSE_PRICE": "101", "TTL_TRD_QNTY": "10", "TURNOVER_LACS": "5", "DELIV_PER": "50"}]
        rows, _ = mdu.clean_day(raw)
        self.assertEqual(sorted(r["symbol"] for r in rows), ["AAA", "FUNDETF"])            # an instrument in the EQ series is kept, whatever it is
        src = (HERE / "market_data_updater.py").read_text()
        self.assertNotRegex(src, r"(?i)\betf\b.*exclude|exclude.*\betf\b")
        self.assertIn("none: every EQ-series stock", src)


# ---------------------------------------------------------------- 5. the publication gate
class GateTests(Tmp):
    def make_private(self, approved_flag=False):
        recs, rows = world(n_per=8)
        write_bhav(self.tmp, {"2026-10-02": [dict(r, close=r["prev_close"]) for r in rows], "2026-10-05": rows})
        sm.write(self.tmp / "private" / "sector_master.json", recs, "2026-10-01")
        self.assertEqual(sdu.run(self.tmp, NOW, gate=SMALL_GATE)[0], 0)

    def test_default_is_false_and_only_the_exact_text_true_approves(self):
        self.assertIs(pub.DEFAULT_APPROVED, False)
        self.assertFalse(pub.approved({}))
        for v in ("", "false", "False", "0", "1", "yes", "on", "truee", "ture", "tru", "enabled"):
            self.assertFalse(pub.approved({pub.ENV_FLAG: v}), v)
        for v in ("true", "True", "TRUE", " true "):
            self.assertTrue(pub.approved({pub.ENV_FLAG: v}), v)
        self.assertEqual(pub.ENV_FLAG, "SECTOR_PUBLIC_DISPLAY_APPROVED")

    def test_not_approved_copies_nothing_to_the_site(self):
        self.make_private()
        site = self.tmp / "_site"
        (site / "out").mkdir(parents=True)
        code, msg = pub.publish(self.tmp, site, {})
        self.assertEqual(code, 0)
        self.assertEqual(list(site.rglob("*.json")), [])
        self.assertIn("not approved", msg)
        self.assertEqual(pub.check_site(site, {}), [])

    def test_not_approved_removes_a_sector_file_that_got_into_the_site(self):
        self.make_private()
        site = self.tmp / "_site"
        (site / "out").mkdir(parents=True)
        shutil.copy(self.tmp / "private" / "market_sectors.json", site / "out" / "market_sectors.json")
        shutil.copy(self.tmp / "private" / "sector_master.json", site / "out" / "renamed.json")        # found by content, not just by name
        self.assertTrue(pub.check_site(site, {}))
        pub.publish(self.tmp, site, {pub.ENV_FLAG: "false"})
        self.assertEqual(list(site.rglob("*.json")), [])

    def test_check_site_finds_a_disguised_sector_file(self):
        self.make_private()
        site = self.tmp / "_site" / "out"
        site.mkdir(parents=True)
        shutil.copy(self.tmp / "private" / "market_sector_stocks.json", site / "data1.json")
        self.assertEqual(len(pub.check_site(site.parent, {})), 1)

    def test_approved_publishes_exactly_the_two_validated_files_with_flag_true(self):
        self.make_private()
        site = self.tmp / "_site"
        code, msg = pub.publish(self.tmp, site, {pub.ENV_FLAG: "true"})
        self.assertEqual(code, 0, msg)
        self.assertEqual(sorted(p.name for p in site.rglob("*") if p.is_file()), ["market_sector_stocks.json", "market_sectors.json"])
        for n in ("market_sectors.json", "market_sector_stocks.json"):
            self.assertIs(json.loads((site / "out" / n).read_text())["public_display_approved"], True)
            self.assertIs(json.loads((self.tmp / "private" / n).read_text())["public_display_approved"], False)     # the private copy never changes
        self.assertEqual(vso.main(["--dir", str(site / "out")]), 0)
        self.assertEqual(pub.check_site(site, {pub.ENV_FLAG: "true"}), [])

    def test_the_master_and_etf_list_are_never_published_even_when_approved(self):
        self.make_private()
        (self.tmp / "private" / "etf_list.json").write_text("{}")
        site = self.tmp / "_site"
        pub.publish(self.tmp, site, {pub.ENV_FLAG: "true"})
        names = {p.name for p in site.rglob("*")}
        self.assertFalse({"sector_master.json", "etf_list.json"} & names)
        leak = site / "out" / "sector_master.json"
        shutil.copy(self.tmp / "private" / "sector_master.json", leak)
        self.assertTrue(pub.check_site(site, {pub.ENV_FLAG: "true"}))

    def test_approved_but_invalid_or_missing_files_are_not_published(self):
        site = self.tmp / "_site"
        code, msg = pub.publish(self.tmp, site, {pub.ENV_FLAG: "true"})
        self.assertEqual(code, 1)
        self.assertFalse(site.exists())
        self.make_private()
        p = self.tmp / "private" / "market_sectors.json"
        d = json.loads(p.read_text())
        d["sectors"][0]["advances"] = 99
        p.write_text(json.dumps(d))
        self.assertEqual(pub.publish(self.tmp, site, {pub.ENV_FLAG: "true"})[0], 1)
        self.assertFalse((site / "out" / "market_sectors.json").exists())

    def test_renamed_sector_files_cannot_bypass_the_gate(self):
        self.make_private()
        site = self.tmp / "_site"
        (site / "out").mkdir(parents=True)
        for i, src in enumerate(["market_sectors.json", "market_sector_stocks.json", "sector_master.json"]):
            for ext in (".json", ".txt", ".dat", ""):
                shutil.copy(self.tmp / "private" / src, site / "out" / ("harmless%d%s" % (i, ext)))
        minified = json.dumps(json.loads((self.tmp / "private" / "market_sectors.json").read_text()), separators=(",", ":"))
        (site / "out" / "min.json").write_text(minified)
        found = {f.name for f in pub.find_sector_files(site)}
        self.assertEqual(len(found), 13)
        (site / "out" / "index.html").write_text("<html>sector</html>")
        (site / "out" / "scans.json").write_text('{"kind": "scans", "as_of": "2026-10-05"}')
        self.assertNotIn("index.html", {f.name for f in pub.find_sector_files(site)})
        self.assertNotIn("scans.json", {f.name for f in pub.find_sector_files(site)})
        pub.publish(self.tmp, site, {})
        self.assertEqual([f.name for f in site.rglob("*") if f.is_file()], ["index.html", "scans.json"])

    def test_cli_exit_codes(self):
        self.make_private()
        site = self.tmp / "_site"
        (site / "out").mkdir(parents=True)
        self.assertEqual(pub.main(["check", "--site", str(site)]), 0)
        shutil.copy(self.tmp / "private" / "market_sectors.json", site / "out" / "market_sectors.json")
        old = os.environ.pop(pub.ENV_FLAG, None)
        try:
            self.assertEqual(pub.main(["check", "--site", str(site)]), 1)
        finally:
            if old is not None:
                os.environ[pub.ENV_FLAG] = old
        self.assertEqual(pub.main([]), 2)


# ---------------------------------------------------------------- 6. repository guards
def _code_without_docstrings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), ast.Constant) and isinstance(n.body[0].value.value, str):
            docs.add(id(n.body[0].value))
    return tree, docs


class GuardTests(unittest.TestCase):
    ALLOWED_UPPER = {"NSE", "EOD", "EQ", "ETF", "IST", "FAILED"}
    KNOWN_SYMBOLS = {"RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI", "BUILDPRO", "NIFTYBEES", "GOLDBEES", "WIPRO"}

    def test_no_hard_coded_stock_symbols_or_isins_in_sector_code(self):
        for name in SECTOR_MODULES:
            tree, docs = _code_without_docstrings(HERE / name)
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                    v = n.value.strip()
                    self.assertNotRegex(v, r"\bIN[EF][A-Z0-9]{9}\b", "%s: ISIN literal %r" % (name, v))
                    for w in re.findall(r"[A-Za-z0-9&]+", v):
                        self.assertNotIn(w.upper(), self.KNOWN_SYMBOLS, "%s: stock symbol %r" % (name, v))
                    if re.fullmatch(r"[A-Z][A-Z0-9&-]{2,19}", v) and v not in self.ALLOWED_UPPER:
                        self.fail("%s: symbol-like uppercase literal %r (sector code must hold no stock-by-stock data)" % (name, v))

    def test_no_stock_to_sector_mapping_literals(self):
        """No dict literal with more than 8 non-field-name string keys and no list with more than 20 strings: reference data belongs in files."""
        for name in SECTOR_MODULES:
            tree, _ = _code_without_docstrings(HERE / name)
            for n in ast.walk(tree):
                if isinstance(n, ast.Dict):
                    self.assertLessEqual(sum(1 for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str) and not re.fullmatch(r"[a-z][a-z0-9_]*", k.value)), 8, name)
                if isinstance(n, (ast.List, ast.Set, ast.Tuple)):
                    self.assertLessEqual(sum(1 for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)), 20, name)

    def test_the_guard_itself_detects_a_hard_coded_symbol(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        p = tmp / "x.py"
        p.write_text('MAP = {"SOMESTOCK": "Banks"}\n')
        tree, docs = _code_without_docstrings(p)
        hits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9&-]{2,19}", n.value) and n.value not in self.ALLOWED_UPPER]
        self.assertEqual(hits, ["SOMESTOCK"])

    def test_provider_logic_is_isolated(self):
        for name in SECTOR_MODULES:
            if name == "sector_provider.py":
                continue
            src = (HERE / name).read_text()
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
            tree = ast.parse(src)
            imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                       {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("upstox_common", imported, name)
            self.assertNotIn("requests", imported, name)
            if name != "sector_master_updater.py":                # its main() names the default provider
                self.assertNotRegex(code, r"(?i)upstox|api\.", name)
        self.assertNotIn("sector_provider", (HERE / "sector_data_updater.py").read_text())

    def test_no_sector_file_sits_in_out(self):
        out = HERE / "out"
        names = [p.name for p in out.glob("*")] if out.is_dir() else []
        self.assertEqual([n for n in names if "sector" in n.lower()], [])

    def test_sector_workflow_publishes_nothing(self):
        text = (HERE / ".github" / "workflows" / "sector_data.yml").read_text()
        active = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
        for bad in ("upload-pages-artifact", "deploy-pages", "upload-artifact", "git push", "git commit", "contents: write", "cp out", "schedule"):
            self.assertNotIn(bad, active, bad)
        self.assertIn('SECTOR_PUBLIC_DISPLAY_APPROVED: "false"', active)
        self.assertIn("workflow_dispatch", active)
        self.assertIn("sector_publish.py check", active)
        self.assertIn("path: private", active)
        self.assertIn("secrets.SECTOR_ETF_LIST_CSV", active)
        self.assertNotRegex(active, r"etf_list\.json\s*$.*git add|--out out/")
        self.assertNotRegex(active, r"path:\s*\|?\s*\n?\s*out\b")

    def test_existing_deploy_workflows_do_not_reference_private_sector_data(self):
        for f in (HERE / ".github" / "workflows").glob("*.yml"):
            if f.name == "sector_data.yml":
                continue
            t = f.read_text()
            self.assertNotRegex(t, r"private/|sector_master|market_sector|SECTOR_PUBLIC", f.name)

    def test_private_folder_is_not_published_by_the_existing_copy_commands(self):
        for f in (HERE / ".github" / "workflows").glob("*.yml"):
            for line in f.read_text().splitlines():
                if line.strip().startswith("cp ") and "_site" in line:
                    self.assertNotIn("private", line)

    def test_protected_files_are_unchanged(self):
        protected = ["fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py", "nse_updater.py",
                     "market_data_updater.py", "market_derive.py", "validate_market_outputs.py", "upstox_common.py", ".github/workflows/update.yml",
                     ".github/workflows/market_data.yml"]
        try:
            r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--"] + protected, cwd=HERE, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git not available")
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        import guard_update_yml
        changed = r.stdout.split()
        if guard_update_yml.PATH in changed and guard_update_yml.approved_change_only(HERE):
            changed.remove(guard_update_yml.PATH)           # the one approved change: the guarded stock-directory copy (exactly two added lines)
        self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
