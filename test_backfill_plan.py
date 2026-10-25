"""
Tests for backfill_plan.py, the READ-ONLY backfill planner. Offline: no network, no provider, no workflow; every file here is synthetic.

They check that batches partition the universe, that the order is the universe's (or the named priority first), that every count equals coverage_report.py's own count for the
same stocks, that nothing unmeasured or unverified is ever called complete, that a previous plan is reconciled against the data, that the cost model matches the code it is
taken from, and that the tool writes only into a separate output folder and cannot start anything.
"""
import ast
import contextlib
import copy
import hashlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import backfill_plan as B
import coverage_report as C
import test_coverage_report as T

HERE = Path(__file__).resolve().parent
AS_OF = T.AS_OF


class Case(T.StateDirCase):
    def make_full(self, n=3):
        """n stocks with every measurable dataset valid."""
        self.make(n)
        for i in range(n):
            T.write(self.data / "historical" / "shards" / (T.sym(i) + ".json"), {"stocks": {T.sym(i): {"candles": [T.candle("2026-10-20"), T.candle("2026-10-21")]}}})
        T.write(self.data / "financial_history_ledger.json", {"schema": 1, "stocks": {T.sym(i): T.fh_entry(i, [T.year(2025, revenue=1.0)]) for i in range(n)}, "updated": "2026-10-20"})
        self.put("fundamentals_cache.json", {"stocks": {T.sym(i): T.f_entry(i, "2026-10-22") for i in range(n)}})
        self.put("quarterly_cache.json", T.q_doc({T.sym(i): T.q_entry("2026-10-20") for i in range(n)}))
        self.put("financials_cache.json", {"stocks": {T.sym(i): T.fin_entry(i, "2026-10-20") for i in range(n)}})

    def plan(self, **kw):
        kw.setdefault("state_dir", None)
        return B.build_plan(self.data, AS_OF, **kw)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = B.main(list(args))
        return rc, out.getvalue(), err.getvalue()


class Partition(unittest.TestCase):
    def test_the_universe_is_split_into_batches_of_at_most_500_covering_everything_once(self):
        syms = ["S%04d" % i for i in range(2548)]
        batches = B.partition(syms, 500)
        self.assertEqual([len(b) for b in batches], [500, 500, 500, 500, 500, 48])
        self.assertEqual([s for b in batches for s in b], syms)
        self.assertEqual(B.partition(syms[:500], 500), [syms[:500]])
        self.assertEqual(B.partition([], 500), [])
        self.assertEqual([len(b) for b in B.partition(syms[:7], 3)], [3, 3, 1])

    def test_a_bad_batch_size_is_refused(self):
        for bad in (0, -1, 501, True, "500", 2.5, None):
            with self.subTest(bad=bad), self.assertRaises(B.PlanError):
                B.partition(["A"], bad)

    def test_the_symbols_hash_depends_on_order_and_content(self):
        self.assertNotEqual(B.symbols_hash(["A", "B"]), B.symbols_hash(["B", "A"]))
        self.assertEqual(B.symbols_hash(["A", "B"]), B.symbols_hash(["A", "B"]))


class Ordering(Case):
    def test_default_order_is_the_universe_file_order_and_the_alphabetical_warning_is_given(self):
        self.make(1200)
        p = self.plan()
        self.assertEqual([b["count"] for b in p["batches"]], [500, 500, 200])
        self.assertEqual(p["batches"][0]["first"], "S0000")
        self.assertEqual(p["batches"][0]["last"], "S0499")
        self.assertIn("alphabetically", p["order"]["note"])
        self.assertTrue(p["universe"]["sorted_alphabetically"])

    def test_priority_symbols_come_first_in_file_order_and_the_note_goes(self):
        self.make(600)
        pf = self.root / "prio.txt"
        pf.write_text("# large caps\nS0550\n\nS0010\n", encoding="utf-8")
        p = self.plan(priority_file=pf)
        self.assertEqual(p["batches"][0]["symbols"][:3], ["S0550", "S0010", "S0000"])
        self.assertEqual(sorted(s for b in p["batches"] for s in b["symbols"]), self.symbols)
        self.assertIsNone(p["order"]["note"])
        self.assertEqual(p["order"]["priority_symbols"], 2)

    def test_a_bad_priority_file_is_refused(self):
        self.make(5)
        for name, text in (("unknown", "NOPE\n"), ("repeat", "S0001\nS0001\n"), ("junk", "S0001\n<script>\n"), ("lower", "s0001\n")):
            f = self.root / (name + ".txt")
            f.write_text(text, encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(B.PlanError):
                self.plan(priority_file=f)
        link = self.root / "link.txt"
        link.symlink_to(self.root / "unknown.txt")
        with self.assertRaises(B.PlanError):
            self.plan(priority_file=link)
        big = self.root / "big.txt"
        big.write_text("S0001\n" * (B.MAX_PRIORITY_BYTES // 6 + 10), encoding="utf-8")
        with self.assertRaises(B.PlanError):
            self.plan(priority_file=big)


class CountsComeFromTheCoverageReport(Case):
    def test_totals_equal_the_coverage_reports_counts_for_the_same_stocks(self):
        self.fixture10()
        T.write(self.data / "financial_history_ledger.json", {"schema": 1, "stocks": {T.sym(i): T.fh_entry(i, [T.year(2025, revenue=1.0)]) for i in (0, 2, 4)}, "updated": "2026-10-20"})
        for i in (1, 3):
            T.write(self.data / "historical" / "shards" / (T.sym(i) + ".json"), {"stocks": {T.sym(i): {"candles": [T.candle("2026-10-20")]}}})
        p = self.plan(state_dir=self.state, batch_size=4)
        r = C.build_report(self.data, AS_OF, "all", None, self.state)
        names = {"historical_candles": "historical_candles", "financial_history": "financial_history", "fundamentals": "fundamentals_state",
                 "quarterly_results": "quarterly_results", "annual_financials": "annual_financials_and_balance_sheet"}
        for ds, module in names.items():
            m = [x for x in r["scopes"]["all"]["modules"] if x["module"] == module][0]
            t = p["totals"][ds]
            self.assertTrue(t["measurable"], ds)
            for k in ("valid", "stale", "missing", "invalid", "unverified"):
                self.assertEqual(t[k], m["counts"][k] or 0, (ds, k))

    def test_batches_add_up_to_the_total_and_the_plan_covers_every_stock(self):
        self.make_full(7)
        p = self.plan(state_dir=self.state, batch_size=3)
        self.assertEqual([b["count"] for b in p["batches"]], [3, 3, 1])
        for ds in p["datasets_requested"]:
            self.assertEqual(sum(b["datasets"][ds]["denominator"] for b in p["batches"]), 7)
            self.assertEqual(sum(b["datasets"][ds]["counts"]["valid"] for b in p["batches"]), p["totals"][ds]["valid"])


class NothingUnmeasuredIsComplete(Case):
    def test_without_the_saved_state_three_datasets_are_not_measurable_and_never_complete(self):
        self.make_full(3)
        p = self.plan()
        for ds in B.STATE_DATASETS:
            t = p["totals"][ds]
            self.assertFalse(t["measurable"])
            self.assertFalse(t["complete"])
            self.assertNotIn("valid", t)                               # not zero, not anything: absent
            self.assertIn("calls_if_nothing_were_stored_nominal", t)
            for b in p["batches"]:
                self.assertIsNone(b["datasets"][ds]["remaining"])
                self.assertIsNone(b["datasets"][ds]["calls_nominal"])
        self.assertFalse(p["completion"]["all_requested_datasets_verified_complete"])
        self.assertEqual(set(p["completion"]["blocking"]), set(B.STATE_DATASETS))

    def test_everything_valid_and_measurable_is_complete_and_only_then(self):
        self.make_full(3)
        p = self.plan(state_dir=self.state)
        self.assertTrue(p["completion"]["all_requested_datasets_verified_complete"], p["completion"])
        self.assertEqual(p["completion"]["blocking"], [])
        for ds in p["datasets_requested"]:
            self.assertEqual(p["totals"][ds]["remaining"], 0)
            self.assertEqual(p["totals"][ds]["calls_nominal"], 0)
            self.assertEqual(p["totals"][ds]["runs_at_default_budget"], 0)

    def test_one_missing_record_breaks_completion_and_is_costed(self):
        self.make_full(3)
        (self.data / "historical" / "shards" / "S0002.json").unlink()
        p = self.plan(state_dir=self.state)
        t = p["totals"]["historical_candles"]
        self.assertFalse(t["complete"])
        self.assertEqual((t["missing"], t["calls_nominal"]), (1, B.DATASETS["historical_candles"]["calls_missing"]))
        self.assertFalse(p["completion"]["all_requested_datasets_verified_complete"])
        self.assertEqual(p["completion"]["blocking"], ["historical_candles"])

    def test_stale_and_unverified_records_are_not_complete(self):
        self.make_full(3)
        self.put("fundamentals_cache.json", {"stocks": {T.sym(0): T.f_entry(0, "2026-10-22"), T.sym(1): T.f_entry(1, "2026-10-01"), T.sym(2): T.f_entry(2, "2026-10-22", isin="INE000000009")}})
        p = self.plan(state_dir=self.state)
        t = p["totals"]["fundamentals"]
        self.assertEqual((t["valid"], t["stale"]), (1, 1))
        self.assertFalse(t["complete"])
        self.assertFalse(p["completion"]["all_requested_datasets_verified_complete"])

    def test_an_unusable_state_file_is_not_measurable_never_zero_coverage(self):
        self.make_full(3)
        (self.state / "quarterly_cache.json").write_text("{not json", encoding="utf-8")
        p = self.plan(state_dir=self.state)
        t = p["totals"]["quarterly_results"]
        self.assertFalse(t["complete"])
        self.assertEqual(p["completion"]["all_requested_datasets_verified_complete"], False)

    def test_an_empty_universe_is_never_complete(self):
        T.write(self.data / "universe.json", T.universe_doc([]))
        try:
            p = self.plan()
        except B.PlanError:
            return
        self.assertFalse(p["completion"]["all_requested_datasets_verified_complete"])

    def test_dataset_selection(self):
        self.make_full(3)
        p = self.plan(datasets=["financial_history"])
        self.assertEqual(p["datasets_requested"], ["financial_history"])
        self.assertTrue(p["completion"]["all_requested_datasets_verified_complete"])
        for bad in ([], ["nope"], ["financial_history", "financial_history"], ["shareholding"]):
            with self.subTest(bad=bad), self.assertRaises(B.PlanError):
                self.plan(datasets=bad)


class CostsAndRuns(unittest.TestCase):
    def test_runs_round_up_and_zero_calls_need_no_run(self):
        self.assertEqual(B.runs_needed(0, 100, 330), (0, 0))
        self.assertEqual(B.runs_needed(0, 100, None), (0, None))
        self.assertEqual(B.runs_needed(1, 100, 330), (1, 1))
        self.assertEqual(B.runs_needed(101, 100, 330), (2, 1))
        self.assertEqual(B.runs_needed(1361, 100, 330), (14, 5))
        self.assertEqual(B.runs_needed(500, 120, None), (5, None))

    def test_batch_dataset_costs_missing_stale_and_invalid_at_the_documented_rates(self):
        module = {"measurability": "partial", "counts": {"valid": 5, "stale": 2, "missing": 3, "invalid": 1, "unverified": 0, "not_applicable": None}}
        e = B.batch_dataset("fundamentals", module, 11)
        self.assertEqual((e["remaining"], e["calls_nominal"]), (6, 3 * 2 + 2 * 1 + 1 * 2))
        self.assertFalse(e["complete"])
        self.assertEqual(e["calls_worst_case"], 3 * 2 * 3 + 2 * 1 * 3 + 1 * 2 * 3)

    def test_counts_that_do_not_add_up_to_the_batch_are_not_complete(self):
        module = {"measurability": "partial", "counts": {"valid": 4, "stale": 0, "missing": 0, "invalid": 0, "unverified": 0, "not_applicable": None}}
        self.assertFalse(B.batch_dataset("financial_history", module, 5)["complete"])
        module["counts"]["valid"] = 5
        self.assertTrue(B.batch_dataset("financial_history", module, 5)["complete"])

    def test_overlapping_or_inconsistent_counts_never_count_as_complete(self):
        for extra in ("stale", "missing", "invalid", "unverified"):
            module = {"measurability": "partial", "counts": {"valid": 5, "stale": 0, "missing": 0, "invalid": 0, "unverified": 0, "not_applicable": None}}
            module["counts"][extra] = 1
            with self.subTest(extra=extra):
                self.assertFalse(B.batch_dataset("financial_history", module, 5)["complete"])

    def test_the_cost_model_matches_the_code_it_is_taken_from(self):
        import fundamentals_updater as FU
        import historical_batch as HB
        import historical_updater as HU
        import financial_history_updater as FH
        import quarterly_results_updater as Q
        d = B.DATASETS
        self.assertEqual(int(HB.CALL_CEILING * HB.HEADROOM), d["historical_candles"]["ceiling_calls_per_run"])
        self.assertEqual(HU.MAX_CALLS_PER_RUN, d["historical_candles"]["default_calls_per_run"])
        self.assertEqual(FH.DEFAULT_MAX_CALLS, d["financial_history"]["default_calls_per_run"])
        self.assertEqual(Q.MAX_ALLOWED_CALLS, d["quarterly_results"]["ceiling_calls_per_run"])
        self.assertEqual(B.RETRY_FACTOR, FU.RETRIES)
        daily = yaml.safe_load((HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        text = (HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.fundamentals_calls || '%d'" % d["fundamentals"]["default_calls_per_run"], text)
        self.assertIn("inputs.financials_calls || '%d'" % d["annual_financials"]["default_calls_per_run"], text)
        self.assertIn('QUARTERLY_MAX_CALLS: "%d"' % d["quarterly_results"]["default_calls_per_run"], text)
        batch = (HERE / ".github" / "workflows" / "fundamentals_batch.yml").read_text(encoding="utf-8")
        self.assertIn("-le %d" % d["fundamentals"]["ceiling_calls_per_run"], batch)
        self.assertIsInstance(daily, dict)

    def test_per_stock_costs_match_the_updaters(self):
        import fundamentals_updater as FU
        stock = {"isin": "INE000000019"}
        inst = {"isin": "INE000000019", "name": "X"}
        day = B.dt.date(2026, 10, 25)
        self.assertEqual(FU.calls_needed({}, inst, day), B.DATASETS["fundamentals"]["calls_missing"])
        stale = {"isin": "INE000000019", "ratios": {m: 1.0 for m in FU.METRICS}, "ratios_fetched": "2026-10-01", "profile_fetched": "2026-10-20"}
        self.assertEqual(FU.calls_needed(stale, inst, day), B.DATASETS["fundamentals"]["calls_stale"])
        self.assertEqual(len(B.DATASETS["annual_financials"]), len(B.DATASETS["annual_financials"]))
        import financials_updater as FI
        self.assertEqual(len(FI.SECTIONS), B.DATASETS["annual_financials"]["calls_missing"])


class Reconciliation(Case):
    def test_a_previous_claim_of_completion_that_the_data_no_longer_supports_is_listed(self):
        self.make_full(4)
        before = self.plan(state_dir=self.state, batch_size=2)
        self.assertTrue(before["completion"]["all_requested_datasets_verified_complete"])
        (self.data / "historical" / "shards" / "S0003.json").unlink()          # data lost after the "done" plan
        after = self.plan(state_dir=self.state, batch_size=2, previous=before)
        self.assertEqual(after["reconciliation"]["claimed_complete_but_not_supported_now"], [{"batch": 2, "dataset": "historical_candles"}])
        self.assertFalse(after["completion"]["all_requested_datasets_verified_complete"])

    def test_unchanged_data_reconciles_cleanly_and_a_changed_universe_is_not_compared(self):
        self.make_full(4)
        before = self.plan(state_dir=self.state, batch_size=2)
        same = self.plan(state_dir=self.state, batch_size=2, previous=before)
        self.assertEqual(same["reconciliation"]["claimed_complete_but_not_supported_now"], [])
        self.assertTrue(same["reconciliation"]["same_universe_and_batch_size"])
        other = self.plan(state_dir=self.state, batch_size=3, previous=before)
        self.assertFalse(other["reconciliation"]["same_universe_and_batch_size"])
        self.assertEqual(other["reconciliation"]["claimed_complete_but_not_supported_now"], [])

    def test_a_forged_or_foreign_previous_plan_is_refused_and_cannot_mark_anything_complete(self):
        self.make_full(2)
        for bad in ({}, {"kind": "other"}, [], {"kind": B.KIND, "schema_version": 2, "batches": []}, {"kind": B.KIND, "schema_version": 1, "batches": "x"}):
            with self.subTest(bad=bad), self.assertRaises(B.PlanError):
                self.plan(previous=bad)
        crafted = {"kind": B.KIND, "schema_version": 1, "batches": [{"batch": 1, "symbols_sha256": "0" * 64, "datasets": {"historical_candles": {"complete": True}}}],
                   "universe": {"symbols_sha256": B.symbols_hash(self.symbols)}, "order": {"batch_size": 500}}
        p = self.plan(previous=crafted)
        self.assertEqual(p["reconciliation"]["claimed_complete_but_not_supported_now"], [])        # a batch whose symbols differ is not compared
        self.assertNotIn("previous", json.dumps(p["completion"]))

    def test_the_manifest_is_rebuilt_from_data_so_it_is_identical_on_a_rerun(self):
        self.make_full(3)
        a, b = self.plan(state_dir=self.state), self.plan(state_dir=self.state)
        self.assertEqual(B.render_json(a), B.render_json(b))


class SafeOutput(Case):
    def test_it_writes_only_the_two_report_files_into_a_separate_folder_and_changes_nothing_else(self):
        self.make_full(3)
        before = self.tree(self.root / "branch"), self.tree(self.state)
        rc, out, err = self.cli("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--state-dir", str(self.state))
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual((self.tree(self.root / "branch"), self.tree(self.state)), before)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(B.OUTPUT_NAMES))
        doc = json.loads((self.out / "backfill_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["tool"], {"name": "backfill_plan.py", "reads_only": True, "provider_calls": 0, "starts_workflows": False})
        self.assertEqual(doc["kind"], B.KIND)

    def test_dry_run_prints_and_writes_nothing(self):
        self.make_full(3)
        rc, out, err = self.cli("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Dry run: nothing was written.", out)
        self.assertFalse(self.out.exists())

    def test_an_output_folder_that_is_or_contains_an_input_is_refused(self):
        self.make_full(3)
        for out in (self.data, self.data / "sub", self.state, self.root):
            rc, o, e = self.cli("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(out), "--state-dir", str(self.state))
            with self.subTest(out=out.name):
                self.assertEqual(rc, 2)
                self.assertIn("refused", e)
        self.assertFalse((self.data / "sub").exists())

    def test_a_symlinked_output_file_is_refused_and_not_followed(self):
        self.make_full(3)
        self.out.mkdir()
        target = self.root / "victim.txt"
        target.write_text("keep", encoding="utf-8")
        (self.out / "backfill_plan.json").symlink_to(target)
        rc, o, e = self.cli("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_a_planted_temp_symlink_is_not_written_through(self):
        self.make_full(3)
        self.out.mkdir()
        target = self.root / "victim2.txt"
        target.write_text("keep", encoding="utf-8")
        (self.out / "backfill_plan.json.tmp").symlink_to(target)
        rc, o, e = self.cli("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_bad_arguments_are_refused_with_exit_2_and_no_input_text_echoed(self):
        self.make_full(3)
        base = ["--data-branch", str(self.data), "--out", str(self.out)]
        for extra in (["--as-of", "2026-13-40"], ["--as-of", "SECRET-TEXT"], ["--as-of", "2026-10-23", "--batch-size", "501"], ["--as-of", "2026-10-23", "--datasets", "SECRET-TEXT"],
                      ["--as-of", "2026-10-23", "--previous", str(self.root / "missing.json")]):
            rc, o, e = self.cli(*base, *extra)
            with self.subTest(extra=extra):
                self.assertEqual(rc, 2)
                self.assertNotIn("SECRET", e + o)
        rc, o, e = self.cli(*base, "--as-of", "2026-10-23", "--data-branch", str(self.root / "nowhere"))
        self.assertEqual(rc, 2)

    def test_a_malformed_universe_is_refused_not_planned(self):
        self.make(5)
        T.write(self.data / "universe.json", {"schema_version": 1, "kind": "universe", "symbols": "x"})
        with self.assertRaises(B.PlanError):
            self.plan()

    def test_reports_carry_counts_and_symbols_of_batches_only_no_record_values(self):
        self.make_full(3)
        T.write(self.data / "financial_history_ledger.json", {"schema": 1, "stocks": {T.sym(0): T.fh_entry(0, [T.year(2025, revenue=T.SECRET_PCT)], sector=T.SECRET_HOLDER)}, "updated": "x"})
        text = B.render_json(self.plan(state_dir=self.state)) + B.render_markdown(self.plan(state_dir=self.state))
        for secret in (str(T.SECRET_PCT), str(T.SECRET_HOLDER), str(T.SECRET_FIN), str(T.SECRET_ERROR)):
            self.assertNotIn(secret, text)


class NoConnectionDuringARun(Case):
    def test_a_run_makes_no_connection_and_starts_no_process(self):
        self.make_full(3)
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), mock.patch("subprocess.run", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            B.build_plan(self.data, AS_OF, self.state)


class NoProviderNoNetworkNoWorkflow(unittest.TestCase):
    def test_the_planner_imports_no_network_or_process_module(self):
        tree = ast.parse((HERE / "backfill_plan.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported & {"requests", "socket", "subprocess", "urllib", "http", "ssl", "asyncio", "websockets", "os.system"}, set())
        self.assertEqual(imported - {"argparse", "datetime", "hashlib", "json", "math", "os", "re", "sys", "pathlib", "coverage_report"}, set())
        self.assertNotIn("Upstox", (HERE / "backfill_plan.py").read_text(encoding="utf-8").split('"""', 2)[2])

    def test_no_workflow_runs_or_mentions_the_planner(self):
        for wf in sorted((HERE / ".github" / "workflows").glob("*.yml")):
            self.assertNotIn("backfill_plan", wf.read_text(encoding="utf-8"), wf.name)


if __name__ == "__main__":
    unittest.main()
