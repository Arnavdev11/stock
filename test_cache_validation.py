"""
Tests for cache_validation.py and .github/workflows/cache_validation.yml. Offline: no network, no Actions, no provider; every file here is synthetic.

These tests do NOT validate any real cache. They check the controls around the one-off workflow: which cache is chosen, what is refused, and that the public
summary is built from an allowlist and cannot carry a value, identifier, symbol or error text.
"""
import ast
import contextlib
import copy
import datetime as dt
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

import cache_validation as V
import coverage_report as C
import test_cache_isolation as ISO
import test_coverage_report as T

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE / ".github" / "workflows" / "cache_validation.yml"
AS_OF = "2026-10-23"
HOSTILE = ("HOSTILE COMPANY NAME", "HOSTILE SECTOR LABEL", "HOSTILE ERROR TEXT", "INE999999990", "OUTSIDEXYZ")


def page(caches, total=None):
    return json.dumps({"total_count": len(caches) if total is None else total, "actions_caches": caches})


def cache(key, created="2026-10-20T10:00:00Z", accessed="2026-10-22T10:00:00Z", size=1000):
    return {"id": 1, "ref": "refs/heads/main", "key": key, "version": "v", "last_accessed_at": accessed, "created_at": created, "size_in_bytes": size}


class SelectCache(unittest.TestCase):
    def test_the_newest_exact_key_is_chosen(self):
        c = V.select_cache(page([cache("nse-data-100", "2026-10-18T00:00:00Z"), cache("nse-data-200", "2026-10-21T00:00:00Z"), cache("nse-data-150", "2026-10-19T00:00:00Z")]))
        self.assertEqual((c["key"], c["size_in_bytes"]), ("nse-data-200", 1000))

    def test_other_workflows_caches_never_match_even_when_newer(self):
        keys = ["nse-data-shp-300", "nse-data-sector-301", "nse-data-", "nse-data-12x", "nse-data-1-2", "side-historical-5", "sector-private-9", "NSE-DATA-77",
                "nse-data-1\n", " nse-data-1", "nse-data-1 ", "xnse-data-1", "nse-data-١٢", "nse-data-" + "9" * 21]
        caches = [cache(k, "2026-10-22T00:00:00Z") for k in keys] + [cache("nse-data-5", "2026-10-01T00:00:00Z")]
        self.assertEqual(V.select_cache(page(caches))["key"], "nse-data-5")

    def test_no_matching_cache_fails_closed(self):
        for caches in ([], [cache("nse-data-shp-1")], [cache("side-market-2")]):
            with self.assertRaises(V.Refused) as cm:
                V.select_cache(page(caches))
            self.assertIn(cm.exception.code, ("no_cache_with_an_exact_nse_data_key",))

    def test_a_tie_in_time_takes_the_higher_run_id(self):
        c = V.select_cache(page([cache("nse-data-9"), cache("nse-data-10")]))
        self.assertEqual(c["key"], "nse-data-10")

    def test_pages_are_joined_and_the_list_must_be_complete(self):
        both = page([cache("nse-data-1")], total=2) + "\n" + page([cache("nse-data-2", "2026-10-21T00:00:00Z")], total=2)
        self.assertEqual(V.select_cache(both)["key"], "nse-data-2")
        for bad in (page([cache("nse-data-1")], total=3), page([cache("nse-data-1")], total=0),
                    page([cache("nse-data-1")], total=2) + page([cache("nse-data-2")], total=3)):
            with self.assertRaises(V.Refused) as cm:
                V.select_cache(bad)
            self.assertEqual(cm.exception.code, "cache_list_incomplete")

    def test_unusable_lists_are_refused(self):
        cases = {"": "cache_list_empty_response", "   \n": "cache_list_empty_response", "not json": "cache_list_not_json", "[]": "cache_list_unexpected_shape",
                 '{"total_count": 1}': "cache_list_unexpected_shape", '{"total_count": "1", "actions_caches": []}': "cache_list_unexpected_shape",
                 '{"total_count": true, "actions_caches": []}': "cache_list_unexpected_shape",
                 page(["nse-data-1"]): "cache_list_unexpected_shape", page([{"id": 1}]): "cache_list_unexpected_shape"}
        for text, code in cases.items():
            with self.subTest(text=text[:30]):
                with self.assertRaises(V.Refused) as cm:
                    V.select_cache(text)
                self.assertEqual(cm.exception.code, code)

    def test_a_matching_cache_with_bad_metadata_is_refused_not_skipped(self):
        for bad in (cache("nse-data-1", created="yesterday"), cache("nse-data-1", created="2026-10-20T10:00:00"), cache("nse-data-1", size=-1),
                    dict(cache("nse-data-1"), size_in_bytes="9"), dict(cache("nse-data-1"), size_in_bytes=True), dict(cache("nse-data-1"), created_at=None)):
            with self.subTest(bad=str(bad)[:60]):
                with self.assertRaises(V.Refused):
                    V.select_cache(page([bad]))

    def test_a_missing_access_time_falls_back_to_the_creation_time(self):
        c = V.select_cache(page([dict(cache("nse-data-1"), last_accessed_at=None)]))
        self.assertEqual(c["last_accessed_at"], c["created_at"])


class StateFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def put_all(self):
        for n in V.STATE_FILES:
            (self.d / n).write_text("{}", encoding="utf-8")

    def test_all_three_present_passes(self):
        self.put_all()
        V.check_files(self.d)

    def test_each_missing_file_is_refused_by_kind_only(self):
        for n in V.STATE_FILES:
            with self.subTest(missing=n):
                self.put_all()
                (self.d / n).unlink()
                with self.assertRaises(V.Refused) as cm:
                    V.check_files(self.d)
                self.assertEqual(cm.exception.code, "state_file_missing_" + n.split("_")[0])

    def test_empty_directory_symlink_are_refused(self):
        self.put_all()
        (self.d / V.STATE_FILES[1]).write_text("", encoding="utf-8")
        with self.assertRaises(V.Refused) as cm:
            V.check_files(self.d)
        self.assertEqual(cm.exception.code, "state_file_empty_financials")
        (self.d / V.STATE_FILES[1]).unlink()
        (self.d / V.STATE_FILES[1]).mkdir()
        with self.assertRaises(V.Refused):
            V.check_files(self.d)
        (self.d / V.STATE_FILES[1]).rmdir()
        (self.d / "elsewhere.json").write_text("{}", encoding="utf-8")
        (self.d / V.STATE_FILES[1]).symlink_to(self.d / "elsewhere.json")
        with self.assertRaises(V.Refused):
            V.check_files(self.d)

    def test_the_published_output_is_not_the_state(self):
        self.put_all()
        (self.d / V.STATE_FILES[1]).unlink()
        (self.d / "financials.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(V.Refused):
            V.check_files(self.d)

    def test_file_facts_are_a_size_and_a_hash_only(self):
        self.put_all()
        f = V.file_facts(self.d)
        self.assertEqual(set(f), set(V.STATE_FILES))
        self.assertEqual(set(f[V.STATE_FILES[0]]), {"bytes", "sha256"})
        self.assertEqual(f[V.STATE_FILES[0]]["bytes"], 2)


class RealReportCase(unittest.TestCase):
    """A real coverage report (made by the merged tool from SYNTHETIC state full of hostile strings), then the allowlist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.state, self.out = self.root / "branch", self.root / "state", self.root / "report"
        self.data.mkdir()
        self.state.mkdir()
        syms = [T.sym(i) for i in range(10)]
        T.write(self.data / "universe.json", T.universe_doc(syms))
        T.write(self.data / "stock_directory.json", T.directory_doc(syms))
        hostile = {"company_name": HOSTILE[0], "sector": HOSTILE[1]}
        T.write(self.state / "fundamentals_cache.json", {"stocks": {
            T.sym(0): dict(T.f_entry(0), **hostile), T.sym(1): dict(T.f_entry(1, "2026-10-10"), isin=HOSTILE[3], **hostile),
            T.sym(4): {"last_error": HOSTILE[2], "last_attempt": "2026-10-22"}, HOSTILE[4]: dict(T.f_entry(2), **hostile)}})
        T.write(self.state / "quarterly_cache.json", T.q_doc({T.sym(0): T.q_entry(), T.sym(5): T.q_entry(quarters={}, problem=HOSTILE[2], failures=2),
                                                              HOSTILE[4]: T.q_entry()}))
        T.write(self.state / "financials_cache.json", {"stocks": {T.sym(0): T.fin_entry(0), T.sym(4): {"last_error": HOSTILE[2], "income": {"error": HOSTILE[2], "attempted": "2026-10-22", "failures": 1}},
                                                                  T.sym(1): dict(T.fin_entry(1), isin=HOSTILE[3])}})
        self.report = C.build_report(self.data, T.AS_OF, "both", None, self.state)

    def prov(self, **kw):
        p = {"CV_MATCHED_KEY": "nse-data-123", "CV_RESTORE_HIT": "true", "CV_RUN_ID": "456", "CV_MAIN_SHA": "a" * 40, "CV_DATA_BRANCH_SHA": "b" * 40, "CV_AS_OF": AS_OF,
             "CV_CREATED_AT": "2026-10-20T10:00:00Z", "CV_LAST_ACCESSED_AT": "2026-10-22T10:00:00Z", "CV_SIZE": "1234"}
        p.update(kw)
        return p

    def render(self, report=None):
        s = V.sanitize(report if report is not None else self.report, AS_OF)
        return s, V.render(s, V.provenance_from_env(self.prov()), {n: {"bytes": 1, "sha256": "c" * 64} for n in V.STATE_FILES})


class Allowlist(RealReportCase):
    def test_the_synthetic_report_really_holds_the_hostile_values(self):
        raw = C.render_json(self.report)
        self.assertIn(HOSTILE[4], raw)                                  # the outside-universe list carries a hostile key
        self.assertIn("S0001", raw)                                     # symbols and ISIN-mismatch lists are in the full report
        self.assertNotIn(HOSTILE[3], raw)                               # (the report itself never writes an ISIN value; the allowlist does not rely on that)

    def test_no_hostile_value_symbol_identifier_or_error_reaches_the_summary(self):
        s, text = self.render()
        both = json.dumps(s) + text
        for bad in HOSTILE + ("S0000", "S0001", "S0004", "S0005", "INE", "PROVIDER"):
            self.assertNotIn(bad, both, bad)
        for key in ("company", "sector", '"isin"', "last_error", "reconciliation", '"symbols"', '"identifiers"', '"message"', '"source_paths"', '"path"', '"details"'):
            self.assertNotIn(key, json.dumps(s), key)               # no such field in the allowlisted object (the prose of the Markdown may use some words)
        self.assertIsNone(re.search(r"\bS\d{4}\b", both))

    def test_the_summary_keeps_the_aggregates_that_matter(self):
        s, text = self.render()
        q = s["scopes"]["all"]["modules"]["quarterly_results"]
        self.assertEqual(q["denominator"], 10)
        self.assertEqual(sum(v for v in q["counts"].values() if v is not None), 10)
        self.assertEqual(q["facts"]["stocks_with_failed_requests"], 1)
        self.assertIn("failed_last_attempt", q["missing_reasons"])
        f = s["scopes"]["all"]["modules"]["fundamentals_state"]
        self.assertIn("isin_differs_from_universe", f["facts"]["unverified_reasons"])
        self.assertEqual(set(s["state_files"]), set(V.STATE_FILES))
        self.assertTrue(all(v["usable"] for v in s["state_files"].values()))
        self.assertIn("cache coverage, not publication status", text)
        self.assertIn("nse-data-123", text)

    def test_only_the_three_state_modules_appear(self):
        s, _ = self.render()
        for label in V.SCOPES:
            self.assertEqual(list(s["scopes"][label]["modules"]), list(V.STATE_MODULES))
        self.assertEqual(set(s), {"generated_for", "state_files", "scopes"})

    def test_extra_fields_are_dropped_not_passed_through(self):
        r = copy.deepcopy(self.report)
        for m in r["scopes"]["all"]["modules"]:
            m["company_name"] = HOSTILE[0]
            m["facts"]["raw_record"] = {"sector": HOSTILE[1]}
            m["unverified"] = [{"reason": HOSTILE[2]}]
        r["raw_cache"] = HOSTILE[3]
        r["scopes"]["all"]["extra"] = HOSTILE[0]
        s, text = self.render(r)
        for bad in HOSTILE:
            self.assertNotIn(bad, json.dumps(s) + text)

    def test_a_problem_text_is_cut_to_its_one_word_code(self):
        r = copy.deepcopy(self.report)
        r["state_files"]["quarterly_cache.json"] = {"usable": False, "problem": "unsupported_schema: %s %s" % (HOSTILE[3], HOSTILE[0]), "entries": None}
        r["state_files"]["financials_cache.json"] = {"usable": False, "problem": "Has Spaces And " + HOSTILE[1], "entries": None}
        s, text = self.render(r)
        self.assertEqual(s["state_files"]["quarterly_cache.json"]["problem"], "unsupported_schema")
        self.assertEqual(s["state_files"]["financials_cache.json"]["problem"], "withheld")
        for bad in HOSTILE:
            self.assertNotIn(bad, text)

    def test_warning_messages_and_symbol_lists_are_dropped_and_only_codes_and_counts_remain(self):
        r = copy.deepcopy(self.report)
        w = [x for m in r["scopes"]["all"]["modules"] for x in m["warnings"]]
        self.assertTrue(w and all("message" in x for x in w))
        s, text = self.render(r)
        self.assertEqual(set(k for sc in s["scopes"].values() for m in sc["modules"].values() for x in m["warnings"] for k in x), {"code", "count"})

    def test_an_unusable_state_file_is_reported_as_unverified_never_as_zero_coverage(self):
        (self.state / "quarterly_cache.json").write_text("{not json " + HOSTILE[0], encoding="utf-8")
        r = C.build_report(self.data, T.AS_OF, "both", None, self.state)
        s, text = self.render(r)
        q = s["scopes"]["all"]["modules"]["quarterly_results"]
        self.assertEqual((q["measurability"], q["counts"]["unverified"], q["counts"]["valid"]), ("not_measurable", 10, None))
        self.assertFalse(s["state_files"]["quarterly_cache.json"]["usable"])
        self.assertEqual(s["state_files"]["quarterly_cache.json"]["problem"], "invalid_json")
        self.assertNotIn(HOSTILE[0], text)


class SchemaFailsClosed(RealReportCase):
    def refuses(self, report, code=None):
        with self.assertRaises(V.Refused) as cm:
            V.sanitize(report, AS_OF)
        if code:
            self.assertEqual(cm.exception.code, code)

    def mods(self, r, label="all"):
        return {m["module"]: m for m in r["scopes"][label]["modules"]}

    def test_wrong_kind_version_date_or_shape(self):
        for k, v in (("kind", "other"), ("schema_version", 999), ("generated_for", "2026-10-22")):
            r = copy.deepcopy(self.report)
            r[k] = v
            self.refuses(r)
        for bad in (None, [], "x", 5):
            self.refuses(bad)
        r = copy.deepcopy(self.report)
        del r["state_files"]
        self.refuses(r, "report_has_no_state_files_block")
        r = copy.deepcopy(self.report)
        r["state_files"]["extra.json"] = {"usable": True}
        self.refuses(r, "report_has_no_state_files_block")
        r = copy.deepcopy(self.report)
        r["state_files"]["quarterly_cache.json"]["usable"] = "yes"
        self.refuses(r, "report_bad_state_files_block")
        r = copy.deepcopy(self.report)
        del r["scopes"]["first_500"]
        self.refuses(r, "report_unexpected_scopes")

    def test_a_missing_or_repeated_state_module(self):
        r = copy.deepcopy(self.report)
        r["scopes"]["all"]["modules"] = [m for m in r["scopes"]["all"]["modules"] if m["module"] != "fundamentals_state"]
        self.refuses(r, "report_state_modules_missing")
        r = copy.deepcopy(self.report)
        r["scopes"]["all"]["modules"].append(copy.deepcopy(self.mods(r)["quarterly_results"]))
        self.refuses(r, "report_repeated_module")

    def test_counts_must_be_plain_non_negative_integers_that_add_up(self):
        def mutate(f):
            r = copy.deepcopy(self.report)
            f(self.mods(r)["quarterly_results"])
            return r
        self.refuses(mutate(lambda m: m["counts"].update(valid=True)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=-1)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid="3")), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=1.5)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=10 ** 9)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=(m["counts"]["valid"] or 0) + 1)), "report_counts_do_not_add_up")
        self.refuses(mutate(lambda m: m["counts"].pop("stale")), "report_bad_counts")
        self.refuses(mutate(lambda m: m["counts"].update(extra=1)), "report_bad_counts")
        self.refuses(mutate(lambda m: m["denominator"].update(value=99)), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(measurability="complete")), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(coverage_pct=float("nan"))), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(coverage_pct=150)), "report_bad_number")

    def test_reason_names_must_be_plain_tokens(self):
        for key in ("S0001 has a problem", "INE999999990", "Has Capitals", "1starts_with_digit", "with-dash", "", "x" * 81, "a\nb", HOSTILE[0]):
            r = copy.deepcopy(self.report)
            self.mods(r)["quarterly_results"]["missing_reasons"][key] = 1
            with self.subTest(key=key[:20]):
                self.refuses(r, "report_bad_reason_name")
        r = copy.deepcopy(self.report)
        self.mods(r)["quarterly_results"]["invalid_reasons"] = {"r%d" % i: 1 for i in range(41)}
        self.refuses(r, "report_bad_reason_counts")
        r = copy.deepcopy(self.report)
        self.mods(r)["quarterly_results"]["missing_reasons"] = ["failed_last_attempt"]
        self.refuses(r, "report_bad_reason_counts")
        r = copy.deepcopy(self.report)
        self.mods(r)["fundamentals_state"]["facts"]["unverified_reasons"]["BAD KEY"] = 1
        self.refuses(r, "report_bad_reason_name")

    def test_facts_warnings_and_problems_must_have_the_reviewed_types(self):
        def mutate(f):
            r = copy.deepcopy(self.report)
            f(self.mods(r)["fundamentals_state"])
            return r
        self.refuses(mutate(lambda m: m["facts"].update(stocks_with_ratios="many")), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(facts=[])), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"].pop("state_file")), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"]["state_file"].update(usable="yes")), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"]["state_file"].update(entries=None)), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(warnings="none")), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "Has Spaces", "count": 1}])), "report_bad_warning")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "ok_code", "count": "1"}])), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "ok_code", "count": 1}] * 21)), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(problems="none")), "report_bad_module")


class Provenance(unittest.TestCase):
    OK = {"CV_MATCHED_KEY": "nse-data-123", "CV_RESTORE_HIT": "true", "CV_RUN_ID": "456", "CV_MAIN_SHA": "a" * 40, "CV_DATA_BRANCH_SHA": "b" * 40, "CV_AS_OF": AS_OF,
          "CV_CREATED_AT": "2026-10-20T10:00:00Z", "CV_LAST_ACCESSED_AT": "2026-10-22T10:00:00.123Z", "CV_SIZE": "1234"}

    def test_a_good_set_passes_and_any_odd_value_is_refused(self):
        self.assertEqual(V.provenance_from_env(self.OK), self.OK)
        for name, bad in (("CV_MATCHED_KEY", "nse-data-shp-1"), ("CV_MATCHED_KEY", "nse-data-1\n"), ("CV_RESTORE_HIT", "false"), ("CV_RUN_ID", "12 34"), ("CV_MAIN_SHA", "A" * 40),
                          ("CV_DATA_BRANCH_SHA", "b" * 39), ("CV_AS_OF", "yesterday"), ("CV_CREATED_AT", "<script>"), ("CV_SIZE", "12.5"), ("CV_SIZE", "")):
            with self.subTest(name=name, bad=bad):
                with self.assertRaises(V.Refused):
                    V.provenance_from_env(dict(self.OK, **{name: bad}))
        env = dict(self.OK)
        del env["CV_RUN_ID"]
        with self.assertRaises(V.Refused):
            V.provenance_from_env(env)


class CommandLine(RealReportCase):
    def run_cli(self, argv, env=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = V.main(["cache_validation.py"] + argv, env if env is not None else {})
        return rc, out.getvalue()

    def test_summarize_writes_the_allowlisted_summary_and_nothing_else(self):
        rep = self.root / "r"
        rep.mkdir()
        (rep / "coverage_report.json").write_text(C.render_json(self.report), encoding="utf-8")
        for n in V.STATE_FILES:
            (self.root / "datadir").mkdir(exist_ok=True)
            (self.root / "datadir" / n).write_text("{}", encoding="utf-8")
        summary = self.root / "summary.md"
        rc, out = self.run_cli(["summarize", str(rep / "coverage_report.json"), str(self.root / "datadir"), str(summary)], self.prov())
        self.assertEqual((rc, out.strip()), (0, "cache_validation: summary written"))
        text = summary.read_text(encoding="utf-8")
        for bad in HOSTILE + ("S0001", "INE"):
            self.assertNotIn(bad, text + out)
        self.assertIn("not publication status", text)

    def test_a_refusal_writes_nothing_and_prints_only_a_fixed_code(self):
        rep = self.root / "r.json"
        bad = copy.deepcopy(self.report)
        bad["scopes"]["all"]["modules"][0]["counts"] = HOSTILE[0]
        rep.write_text(json.dumps(bad), encoding="utf-8")
        for n in V.STATE_FILES:
            (self.root / "d").mkdir(exist_ok=True)
            (self.root / "d" / n).write_text("{}", encoding="utf-8")
        summary = self.root / "summary.md"
        rc, out = self.run_cli(["summarize", str(rep), str(self.root / "d"), str(summary)], self.prov())
        self.assertEqual(rc, 0)                                         # the unrelated module was not read: only the three state modules are
        rc, out = self.run_cli(["summarize", str(rep), str(self.root / "d"), str(self.root / "s2.md")], self.prov(CV_AS_OF="2026-10-22"))
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (report_date_mismatch)"))
        self.assertFalse((self.root / "s2.md").exists())

    def test_garbage_input_prints_no_input_text(self):
        junk = self.root / "junk.json"
        junk.write_text("SECRET-LOOKING TEXT " + HOSTILE[0], encoding="utf-8")
        for argv in (["select", str(junk)], ["summarize", str(junk), str(self.root), str(self.root / "o.md")], ["select", str(self.root / "missing.json")]):
            rc, out = self.run_cli(argv, self.prov())
            self.assertEqual(rc, 1)
            self.assertRegex(out.strip(), r"^cache_validation: refused \([a-z_]+\)$")
            self.assertNotIn("SECRET", out)
            self.assertNotIn(HOSTILE[0], out)

    def test_select_writes_the_github_output_lines(self):
        lst = self.root / "list.json"
        lst.write_text(page([cache("nse-data-77", size=5)]), encoding="utf-8")
        go = self.root / "gh_output"
        rc, _ = self.run_cli(["select", str(lst)], {"GITHUB_OUTPUT": str(go)})
        self.assertEqual(rc, 0)
        self.assertEqual(go.read_text().split("\n")[0], "key=nse-data-77")
        rc, out = self.run_cli(["select", str(lst)])
        self.assertIn("key=nse-data-77", out)

    def test_usage_error(self):
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["select"])[0], 2)


class WorkflowControls(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.text)
        self.steps = self.doc["jobs"]["validate"]["steps"]
        self.code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))

    def test_manual_only_with_minimal_permissions_and_the_ledger_group(self):
        self.assertEqual(self.doc["on"], {"workflow_dispatch": None})
        self.assertEqual(self.doc["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(self.doc["concurrency"], {"group": "stocklens-ledger", "cancel-in-progress": False})
        self.assertEqual(list(self.doc["jobs"]), ["validate"])
        self.assertNotIn("permissions", self.doc["jobs"]["validate"])

    def test_only_these_actions_are_used_and_none_saves_uploads_or_deploys(self):
        uses = [s["uses"] for s in self.steps if "uses" in s]
        self.assertEqual(sorted(set(uses)), ["actions/cache/restore@v4", "actions/checkout@v4", "actions/setup-python@v5"])
        for banned in ("cache/save", "actions/cache@", "upload-artifact", "upload-pages", "deploy-pages", "configure-pages", "download-artifact", "gh-pages",
                       "git push", "git commit", "gh workflow", "gh run", "gh cache", "gh api -X", "--method", "curl", "wget", "pip install", "UPSTOX", "secrets.", "id-token"):
            self.assertNotIn(banned, self.code, banned)

    def test_the_restore_is_one_exact_key_with_the_daily_path_set_and_no_prefix(self):
        r = [s for s in self.steps if s.get("uses") == "actions/cache/restore@v4"]
        self.assertEqual(len(r), 1)
        w = r[0]["with"]
        self.assertEqual(set(w), {"path", "key", "fail-on-cache-miss"})
        self.assertNotIn("restore-keys", self.code)
        self.assertIs(w["fail-on-cache-miss"], True)
        self.assertEqual(w["key"], "${{ steps.select.outputs.key }}")
        self.assertEqual([p.strip() for p in w["path"].splitlines() if p.strip()], ["data", "out"])
        self.assertNotIn("lookup-only", self.code)

    def test_the_restore_paths_equal_the_daily_runs_so_the_cache_version_matches(self):
        daily = {p for name in ("update.yml", "fundamentals_batch.yml") for _, kind, _, _, p, _ in ISO.caches(yaml.safe_load((WORKFLOW.parent / name).read_text(encoding="utf-8")))
                 if kind in ("both", "restore")}
        mine = {p for _, kind, _, _, p, _ in ISO.caches(self.doc)}
        self.assertEqual(mine, daily)
        self.assertEqual(mine, {frozenset({"data", "out"})})

    def test_it_is_a_restore_only_member_of_the_ledger_group_and_breaks_the_isolation_rules_nowhere(self):
        mine = ISO.caches(self.doc)
        self.assertEqual([(kind, prefixes, group) for _, kind, _, prefixes, _, group in mine], [("restore", [], ISO.LEDGER)])
        allc = ISO.load_all(lambda n: (WORKFLOW.parent / n).read_text(encoding="utf-8"))
        self.assertEqual(ISO.violations(allc), [])

    def test_the_selection_pattern_is_the_exact_one(self):
        self.assertEqual(V.CACHE_KEY_RE.pattern.replace("{1,20}", "+"), "nse-data-[0-9]+")
        self.assertIn("python cache_validation.py select", self.code)
        self.assertIn("python cache_validation.py check-files data", self.code)
        self.assertIn("python cache_validation.py summarize", self.code)

    def test_the_report_is_offline_quiet_and_written_outside_the_workspace(self):
        run = [s["run"] for s in self.steps if "coverage_report.py" in s.get("run", "")]
        self.assertEqual(len(run), 1)
        self.assertIn('--out "$RUNNER_TEMP/report"', run[0])
        self.assertIn("--state-dir data", run[0])
        self.assertIn("> /dev/null 2>&1", run[0])

    def test_the_summary_goes_only_to_the_job_summary(self):
        self.assertIn('"$GITHUB_STEP_SUMMARY"', self.code)
        self.assertEqual(self.code.count("GITHUB_STEP_SUMMARY"), 1)

    def test_the_default_branch_guard_comes_first(self):
        self.assertIn("default branch", self.steps[0]["name"].lower())

    def test_the_only_token_is_the_read_only_workflow_token_used_to_list_metadata(self):
        tokens = [(s.get("name"), s["env"]["GH_TOKEN"]) for s in self.steps if "GH_TOKEN" in s.get("env", {})]
        self.assertEqual(tokens, [("List cache metadata (no content)", "${{ github.token }}")])

    def test_no_step_prints_a_file_or_record(self):
        for banned in ("cat ", "head ", "tail ", "jq ", "echo \"$(", "python -c", "print("):
            self.assertNotIn(banned, self.code, banned)

    def test_the_helper_is_stdlib_only_and_offline(self):
        tree = ast.parse((HERE / "cache_validation.py").read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add((n.module or "").split(".")[0])
        self.assertEqual(names, {"datetime", "hashlib", "json", "math", "os", "re", "sys", "pathlib", "coverage_report"})
        self.assertIn("provider_calls", (HERE / "coverage_report.py").read_text(encoding="utf-8"))


class DocumentationStatesTheLimits(unittest.TestCase):
    def test_the_document_says_what_the_result_is_and_is_not(self):
        text = (HERE / "CACHE_VALIDATION.md").read_text(encoding="utf-8")
        for needle in ("cache coverage, not publication status", "has not been run", "default branch", "No artifact", "nse-data-"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
