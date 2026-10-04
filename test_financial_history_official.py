"""Phase 4 Step 4B tests: official annual-report provenance in the financial-history ledger (TCS and ITC FY2022 only).
Mocks only; no token. Run: python3 test_financial_history_official.py"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import financial_history_updater as fh                      # noqa: E402
from test_financial_history import TODAY, YEARS4, rec, stock, up_cash, up_income   # noqa: E402

OFFICIAL = fh.OFFICIAL_PROVIDER
TCS_URL = "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2021-22/q4/IND%20AS/Consolidated.pdf"
ITC_URL = "https://www.itcportal.com/about-itc/shareholder-value/annual-reports/itc-annual-report-2022/pdf/consolidated-financial-statements.pdf"


def load_real():
    return json.loads((ROOT / "official_financial_records.json").read_text(encoding="utf-8"))


def real(sym):
    return next(r for r in load_real()["records"] if r["symbol"] == sym)


def fx(**over):
    """A small valid official record (fake numbers) for mechanics tests."""
    u = "https://example.com/report.pdf"
    r = {"symbol": "TCS", "fy": 2022, "period": "Mar 2022", "basis": "consolidated", "company": "Test Co", "accounting_basis": "Ind AS",
         "units": "INR crore; EPS in INR", "retrieved_on": TODAY,
         "values": {"revenue": 100.0, "total_revenue": 110.0, "profit_before_tax": 30.0, "profit_after_tax": 22.0, "eps_basic": 5.0, "eps_diluted": 5.0, "operating_cash_flow": 40.0},
         "fields": {f: {"document_url": u, "page": 3, "exact_label": "label " + f} for f in ("revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "eps_diluted", "operating_cash_flow")},
         "notes": [], "cross_checks": []}
    r.update(over)
    return r


def doc_of(*records):
    return {"schema": 1, "note": "n", "records": list(records)}


def full_value_dict(**over):
    v = {f: None for f in fh.VALUE_FIELDS}
    v.update(over)
    return v


def apply(s, r, today=TODAY):
    return fh.apply_official(s, r, today)


def out_doc(*stocks):
    return fh.build_output({"stocks": {s["symbol"]: s for s in stocks}}, [], [], TODAY)


def with_upstox(sym="TCS"):
    s = stock(sym)
    up_income(s, YEARS4); up_cash(s)
    return s


class ProviderTests(unittest.TestCase):
    def test_1_official_provider_is_accepted(self):
        s = with_upstox(); apply(s, fx())
        self.assertEqual(rec(s, 2022)["source"]["provider"], OFFICIAL)
        self.assertEqual(fh.validate_doc(out_doc(s)), [])

    def test_2_upstox_provider_is_still_accepted(self):
        s = with_upstox()
        self.assertEqual(rec(s, 2026)["source"]["provider"], "Upstox")
        self.assertEqual(fh.validate_doc(out_doc(s)), [])

    def test_unknown_provider_is_rejected(self):
        s = with_upstox(); apply(s, fx())
        d = out_doc(s); rec_ = d["stocks"]["TCS"]["years"][-1]
        rec_["source"]["provider"] = "Some Aggregator"
        self.assertTrue(any("bad source" in p for p in fh.validate_doc(d)))


class SourceMetadataTests(unittest.TestCase):
    def doc_with(self, mutate):
        s = with_upstox(); apply(s, fx())
        d = out_doc(s); r = d["stocks"]["TCS"]["years"][-1]
        mutate(r["source"])
        return fh.validate_doc(d)

    def test_3_valid_metadata_passes(self):
        self.assertEqual(self.doc_with(lambda src: None), [])

    def test_3_each_required_metadata_item_is_enforced(self):
        cases = {
            "no accounting basis": lambda src: src.update(accounting_basis=""),
            "bad retrieved_on": lambda src: src.update(retrieved_on="yesterday"),
            "no document_url": lambda src: src["fields"]["revenue"].pop("document_url"),
            "http url": lambda src: src["fields"]["revenue"].update(document_url="http://example.com/a.pdf"),
            "url with space": lambda src: src["fields"]["revenue"].update(document_url="https://example.com/a b.pdf"),
            "no page": lambda src: src["fields"]["revenue"].pop("page"),
            "page zero": lambda src: src["fields"]["revenue"].update(page=0),
            "no label": lambda src: src["fields"]["revenue"].update(exact_label=""),
            "field without a value": lambda src: src["fields"].update(capex={"document_url": "https://example.com/a.pdf", "page": 1, "exact_label": "x"}),
            "value without provenance": lambda src: src["fields"].pop("profit_before_tax"),
            "fields not an object": lambda src: src.update(fields=[]),
        }
        for name, mutate in cases.items():
            self.assertTrue(self.doc_with(mutate), name)

    def test_the_source_never_carries_upstox_endpoint_metadata(self):
        s = with_upstox(); apply(s, fx())
        src = rec(s, 2022)["source"]
        self.assertNotIn("endpoint", json.dumps(src)); self.assertNotIn("Upstox", json.dumps(src))

    def test_validating_the_curated_file(self):
        self.assertEqual(fh.validate_official(load_real()), [])
        bad = load_real(); bad["records"][0]["values"]["revenue"] = "191754"
        self.assertTrue(fh.validate_official(bad))
        bad = load_real(); del bad["records"][0]["fields"]["revenue"]
        self.assertTrue(fh.validate_official(bad))
        bad = load_real(); bad["records"][0]["basis"] = "standalone-ish"
        self.assertTrue(fh.validate_official(bad))
        bad = load_real(); bad["records"][0]["fy"] = 2021
        self.assertTrue(fh.validate_official(bad))                                         # fy must match the period
        bad = load_real(); bad["records"].append(copy.deepcopy(bad["records"][0]))
        self.assertTrue(fh.validate_official(bad))                                         # duplicate (symbol, fy, basis)


class EpsTests(unittest.TestCase):
    def test_4_tcs_combined_eps_is_not_split(self):
        r = real("TCS")
        self.assertIsNone(r["values"]["eps_diluted"]); self.assertIsNotNone(r["values"]["eps_basic"])
        self.assertNotIn("eps_diluted", r["fields"])
        self.assertIn("combined basic-and-diluted", " ".join(r["notes"]))
        self.assertIn("Basic and diluted", r["fields"]["eps_basic"]["exact_label"])
        s = with_upstox("TCS"); apply(s, r)
        self.assertIsNone(rec(s, 2022)["values"]["eps_diluted"])
        self.assertNotIn("eps_diluted", rec(s, 2022)["source"]["fields"])
        self.assertEqual(fh.validate_doc(out_doc(s)), [])

    def test_5_itc_has_separate_basic_and_diluted_eps(self):
        r = real("ITC")
        self.assertIsNotNone(r["values"]["eps_basic"]); self.assertIsNotNone(r["values"]["eps_diluted"])
        self.assertIn("eps_basic", r["fields"]); self.assertIn("eps_diluted", r["fields"])
        self.assertEqual(r["fields"]["eps_basic"]["exact_label"], "(1) Basic (in \u20b9)")
        self.assertEqual(r["fields"]["eps_diluted"]["exact_label"], "(2) Diluted (in \u20b9)")
        self.assertEqual(r["fields"]["eps_basic"]["heading"], "Earnings per equity share")
        s = with_upstox("ITC"); apply(s, r)
        v = rec(s, 2022)["values"]
        self.assertEqual((v["eps_basic"], v["eps_diluted"]), (12.37, 12.37))

    def test_a_diluted_eps_is_never_invented_from_basic(self):
        s = with_upstox(); apply(s, fx(values=dict(fx()["values"], eps_diluted=None), fields={k: v for k, v in fx()["fields"].items() if k != "eps_diluted"}))
        self.assertIsNone(rec(s, 2022)["values"]["eps_diluted"])


class CoverageTests(unittest.TestCase):
    def build(self):
        tcs, itc, infy = with_upstox("TCS"), with_upstox("ITC"), with_upstox("INFY")
        for s in (tcs, itc, infy):
            for r in load_real()["records"]:
                if r["symbol"] == s["symbol"]:
                    apply(s, r)
        return tcs, itc, infy

    def test_6_no_fy2022_leakage_into_other_stocks(self):
        _, _, infy = self.build()
        self.assertEqual(sorted({r["fy"] for r in infy["years"]}), [2023, 2024, 2025, 2026])
        self.assertIsNone(rec(infy, 2022))

    def test_6_apply_official_ignores_records_for_other_symbols(self):
        infy = with_upstox("INFY")
        res = apply(infy, real("TCS"))
        self.assertEqual(res["status"], "wrong_symbol"); self.assertIsNone(rec(infy, 2022))

    def test_6_the_curated_file_holds_only_tcs_and_itc_fy2022(self):
        recs = load_real()["records"]
        self.assertEqual(sorted((r["symbol"], r["fy"], r["basis"]) for r in recs), [("ITC", 2022, "consolidated"), ("TCS", 2022, "consolidated")])

    def test_7_existing_upstox_records_are_unchanged(self):
        for sym in ("TCS", "ITC"):
            s = with_upstox(sym)
            before = copy.deepcopy([r for r in s["years"] if r["fy"] >= 2023])
            apply(s, real(sym))
            self.assertEqual([r for r in s["years"] if r["fy"] >= 2023], before)

    def test_8_mixed_source_five_year_history_is_valid(self):
        tcs, itc, _ = self.build()
        for s in (tcs, itc):
            self.assertEqual([r["fy"] for r in s["years"]], [2026, 2025, 2024, 2023, 2022])
            self.assertEqual([r["source"]["provider"] for r in s["years"]], ["Upstox"] * 4 + [OFFICIAL])
            self.assertEqual(s["gaps"], {}); self.assertEqual(s["fiscal_year_end_month"], 3)
        self.assertEqual(fh.validate_doc(out_doc(tcs, itc)), [])

    def test_9_missing_fy2022_stays_missing_for_others(self):
        _, _, infy = self.build()
        self.assertEqual(infy["gaps"], {})                                                  # nothing bridged; FY2022 simply absent
        self.assertEqual(min(r["fy"] for r in infy["years"]), 2023)
        s = stock("MARUTI"); up_income(s, YEARS4)
        self.assertEqual(min(r["fy"] for r in s["years"]), 2023)

    def test_a_official_year_does_not_make_gaps_in_the_middle(self):
        s = stock(); up_income(s, ["Mar_2026", "Mar_2025"])
        apply(s, fx())                                                                      # FY2022 present, FY2023 and FY2024 absent
        self.assertEqual(s["gaps"], {"consolidated": [2024, 2023]})                       # reported honestly, never bridged
        self.assertIsNone(rec(s, 2023)); self.assertIsNone(rec(s, 2024))

    def test_b_standalone_and_consolidated_do_not_mix(self):
        s = with_upstox(); apply(s, fx(basis="standalone"))
        self.assertEqual(rec(s, 2022, "standalone")["basis"], "standalone"); self.assertIsNone(rec(s, 2022, "consolidated"))


class ProvenanceTests(unittest.TestCase):
    def test_10_official_provenance_is_complete_and_exact(self):
        s = with_upstox("TCS"); apply(s, real("TCS"))
        src = rec(s, 2022)["source"]
        self.assertEqual(src["provider"], OFFICIAL)
        self.assertEqual((src["company"], src["accounting_basis"], src["retrieved_on"]), ("Tata Consultancy Services Limited", "Ind AS", "2026-10-04"))
        self.assertEqual(src["fields"]["revenue"], {"document_url": TCS_URL, "page": 3, "exact_label": "Revenue from operations"})
        self.assertEqual(src["fields"]["operating_cash_flow"], {"document_url": TCS_URL, "page": 8, "exact_label": "Net cash generated from operating activities"})
        self.assertEqual(src["fields"]["eps_basic"]["page"], 4)
        s = with_upstox("ITC"); apply(s, real("ITC"))
        src = rec(s, 2022)["source"]
        self.assertEqual(src["fields"]["total_revenue"], {"document_url": ITC_URL, "page": 235, "exact_label": "Total Income (I + II)"})
        self.assertEqual(src["fields"]["operating_cash_flow"]["page"], 238)
        self.assertEqual({f: (m["page"], m["document_url"]) for f, m in src["fields"].items()},
                         {**{f: (235, ITC_URL) for f in ("revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "eps_diluted")},
                          "operating_cash_flow": (238, ITC_URL)})
        self.assertEqual({f: m["exact_label"] for f, m in src["fields"].items()},
                         {"revenue": "Revenue From Operations", "total_revenue": "Total Income (I + II)", "profit_before_tax": "Profit before tax (VI + VII)",
                          "profit_after_tax": "Profit for the year (VIII - IX)", "eps_basic": "(1) Basic (in \u20b9)",
                          "eps_diluted": "(2) Diluted (in \u20b9)", "operating_cash_flow": "Net Cash from Operating Activities"})
        self.assertEqual({f: m.get("heading") for f, m in src["fields"].items() if f.startswith("eps")},
                         {"eps_basic": "Earnings per equity share", "eps_diluted": "Earnings per equity share"})
        self.assertNotIn("heading", src["fields"]["revenue"])
        s = with_upstox("TCS"); apply(s, real("TCS"))
        self.assertEqual({f: (m["page"], m["document_url"]) for f, m in rec(s, 2022)["source"]["fields"].items()},
                         {**{f: (3, TCS_URL) for f in ("revenue", "total_revenue", "profit_before_tax", "profit_after_tax")}, "eps_basic": (4, TCS_URL), "operating_cash_flow": (8, TCS_URL)})
        s = with_upstox("ITC"); apply(s, real("ITC"))
        self.assertTrue(rec(s, 2022)["source"]["cross_checks"])

    def test_10_upstox_provenance_is_never_overwritten(self):
        s = with_upstox()
        before = copy.deepcopy(rec(s, 2026)["source"])
        apply(s, fx(fy=2026, period="Mar 2026"))                                            # an official FY2026 must not replace Upstox's FY2026
        self.assertEqual(rec(s, 2026)["source"], before)
        self.assertEqual(rec(s, 2026)["source"]["provider"], "Upstox")

    def test_10_a_later_upstox_fetch_never_overwrites_official_provenance(self):
        s = with_upstox(); apply(s, fx())
        before = copy.deepcopy(rec(s, 2022))
        up_income(s, ["Mar_2022"], fetched="2026-12-01", base=5000.0)                       # Upstox starts returning FY2022 later
        self.assertEqual(rec(s, 2022), before)

    def test_values_are_exactly_the_verified_numbers(self):
        v = real("TCS")["values"]
        self.assertEqual((v["revenue"], v["total_revenue"], v["profit_before_tax"], v["profit_after_tax"], v["eps_basic"], v["operating_cash_flow"]),
                         (191754.0, 195772.0, 51687.0, 38449.0, 103.62, 39949.0))
        v = real("ITC")["values"]
        self.assertEqual((v["revenue"], v["total_revenue"], v["profit_before_tax"], v["profit_after_tax"], v["eps_basic"], v["eps_diluted"], v["operating_cash_flow"]),
                         (65204.96, 67041.31, 20740.47, 15503.13, 12.37, 12.37, 15775.51))

    def test_unreported_fields_stay_null_never_zero(self):
        s = with_upstox(); apply(s, real("TCS"))
        v = rec(s, 2022)["values"]
        for f in ("investing_cash_flow", "financing_cash_flow", "capex", "free_cash_flow", "eps_diluted"):
            self.assertIsNone(v[f], f)
        self.assertEqual(rec(s, 2022)["missing"], ["investing_cash_flow", "financing_cash_flow"])

    def test_the_record_gets_status_and_first_seen(self):
        s = with_upstox(); apply(s, fx())
        r = rec(s, 2022)
        self.assertEqual((r["verification"]["status"], r["verification"]["reconciliation"]), ("verified", "not_checked"))
        self.assertEqual((r["first_seen"], r["last_confirmed"], r["period_end"]), (TODAY, TODAY, "2022-03-31"))

    def test_notes_are_kept_on_the_record(self):
        s = with_upstox("ITC"); apply(s, real("ITC"))
        self.assertTrue(any("associates" in n for n in rec(s, 2022)["notes"]))


class ItcPresentationNoteTests(unittest.TestCase):
    """The ITC FY2022 PBT/PAT decision: keep the Annual Report presentation (includes the share of associates/JVs)."""

    def notes(self):
        return " ".join(real("ITC")["notes"])

    def test_the_kept_values_are_the_annual_report_presentation(self):
        v = real("ITC")["values"]
        self.assertEqual((v["profit_before_tax"], v["profit_after_tax"], v["total_revenue"]), (20740.47, 15503.13, 67041.31))
        self.assertEqual(real("ITC")["fields"]["profit_before_tax"]["page"], 235)
        self.assertEqual(real("ITC")["fields"]["profit_before_tax"]["document_url"], ITC_URL)

    def test_the_note_explains_the_associates_presentation(self):
        n = self.notes()
        for needle in ("Annual Report", "share of profit of associates and joint ventures", "20,722.99", "15,485.65", "presents the same amounts separately", "reclassified"):
            self.assertIn(needle, n, needle)

    def test_the_note_records_the_upstox_confirmation(self):
        n = self.notes()
        for needle in ("25,915.12", "19,476.72", "follows the Annual Report presentation"):
            self.assertIn(needle, n, needle)

    def test_it_is_described_as_presentation_not_restatement(self):
        text = (self.notes() + " " + " ".join(c["result"] for c in real("ITC")["cross_checks"])).lower()
        self.assertIn("presentation", text); self.assertIn("reclassif", text)
        self.assertNotIn("restate", text)                                                   # never called a restatement
        self.assertNotIn("restatement", text)

    def test_the_cross_check_names_the_results_document_and_the_difference(self):
        cc = real("ITC")["cross_checks"]
        self.assertEqual(len(cc), 1)
        self.assertIn("annual%20financial%20results%20-%20fy%202022-23.pdf", cc[0]["document_url"])
        self.assertIn("20,722.99", cc[0]["result"]); self.assertIn("presentation/reclassification difference", cc[0]["result"])

    def test_the_record_carries_the_notes_into_the_ledger(self):
        s = with_upstox("ITC"); apply(s, real("ITC"))
        self.assertEqual(rec(s, 2022)["notes"], real("ITC")["notes"])
        self.assertEqual(rec(s, 2022)["source"]["cross_checks"], real("ITC")["cross_checks"])

    def test_a_blank_eps_heading_is_rejected(self):
        bad = load_real(); bad["records"][1]["fields"]["eps_basic"]["heading"] = ""
        self.assertTrue(fh.validate_official(bad))


class RevisionTests(unittest.TestCase):
    def test_reapplying_the_same_record_is_a_no_op(self):
        s = with_upstox(); apply(s, fx())
        before = copy.deepcopy(rec(s, 2022))
        res = apply(s, fx(), "2026-11-01")
        after = rec(s, 2022)
        self.assertEqual(res["status"], "confirmed"); self.assertEqual(after["revisions"], [])
        self.assertEqual({k: v for k, v in after.items() if k != "last_confirmed"}, {k: v for k, v in before.items() if k != "last_confirmed"})

    def test_a_changed_official_value_keeps_the_old_one(self):
        s = with_upstox(); apply(s, fx())
        changed = fx(); changed["values"]["revenue"] = 101.5
        res = apply(s, changed, "2026-11-01")
        r = rec(s, 2022)
        self.assertEqual(res["status"], "revised")
        self.assertEqual(r["values"]["revenue"], 101.5)
        self.assertEqual(r["revisions"][0]["values"], {"revenue": 100.0}); self.assertEqual(r["revisions"][0]["superseded_on"], "2026-11-01")
        self.assertEqual(fh.validate_doc(out_doc(s)), [])

    def test_a_null_in_the_curated_file_never_erases_a_value(self):
        s = with_upstox(); apply(s, fx())
        slim = fx(); slim["values"]["profit_after_tax"] = None; del slim["fields"]["profit_after_tax"]
        apply(s, slim, "2026-11-01")
        self.assertEqual(rec(s, 2022)["values"]["profit_after_tax"], 22.0)

    def test_fiscal_year_end_conflict_is_refused(self):
        s = with_upstox()
        res = apply(s, fx(period="Dec 2022"))
        self.assertEqual(res["status"], "refused"); self.assertIsNone(rec(s, 2022))


class IntegrationTests(unittest.TestCase):
    def test_apply_all_only_touches_symbols_in_the_ledger(self):
        ledger = {"stocks": {"TCS": with_upstox("TCS"), "INFY": with_upstox("INFY")}}
        res = fh.apply_all_official(ledger, load_real(), TODAY)
        self.assertIsNotNone(rec(ledger["stocks"]["TCS"], 2022)); self.assertIsNone(rec(ledger["stocks"]["INFY"], 2022))
        self.assertNotIn("ITC", ledger["stocks"])                                           # a stock Upstox never produced is not created
        self.assertEqual(res["applied"], 1); self.assertEqual(res["skipped"], [{"symbol": "ITC", "reason": "not in ledger"}])

    def test_document_level_source_text_names_both_providers(self):
        d = out_doc(with_upstox())
        self.assertIn("Upstox", d["source"]); self.assertIn("official", d["source"].lower())

    def test_main_applies_the_official_records_before_validation(self):
        import inspect
        body = inspect.getsource(fh.main)
        call = "apply_all_official(ledger, official, day)"
        self.assertIn("= " + call, body)                                                     # a real call, not a comment
        self.assertLess(body.index(call), body.index("validate_doc"))

    def test_protected_files_do_not_use_the_official_file(self):
        for name in ("index.html", "financials_updater.py", "fundamentals_updater.py", "nse_updater.py", "historical_updater.py"):
            self.assertNotIn("official_financial_records", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_the_workflow_still_has_no_extra_triggers(self):
        wf = (ROOT / ".github/workflows/financial_history.yml").read_text(encoding="utf-8")
        self.assertNotIn("push:", wf)


if __name__ == "__main__":
    unittest.main(verbosity=0)
