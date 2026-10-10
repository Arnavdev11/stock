"""
test_shareholding.py - Phase 5H.3 tests for shareholding_updater.py. Standard unittest, no network, no token.
Run:  python3 test_shareholding.py
XBRL files are built synthetically (make_xbrl) with the same structure as the NSE filings the Phase 5H.2B probe read.
"""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import shareholding_ledger as sl
import shareholding_updater as su

NS = 'xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/2020"'
SECRET = "SECRET-TOKEN-DO-NOT-PRINT-123"


def make_xbrl(rows, report="2025-12-31", extra=None, dates=True):
    """rows: [(member, shares, pct)] for the report date. extra: [(period, member, shares, pct)] for other periods."""
    ctx, facts, n = [], [], 0

    def add(member, period, shares, pct):
        nonlocal n
        n += 1
        cid = "c%d" % n
        ctx.append('<context id="%s"><entity><identifier scheme="x">1</identifier>'
                   '<segment><xbrldi:explicitMember dimension="in-bse-shp:CategoryOfShareholdersAxis">in-bse-shp:%s</xbrldi:explicitMember></segment></entity>'
                   '<period><instant>%s</instant></period></context>' % (cid, member, period))
        if shares is not None:
            facts.append('<in-bse-shp:NumberOfFullyPaidUpEquityShares contextRef="%s" unitRef="u">%s</in-bse-shp:NumberOfFullyPaidUpEquityShares>' % (cid, shares))
        if pct is not None:
            facts.append('<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="%s" unitRef="u">%s</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>' % (cid, pct))

    for m, s, p in rows:
        add(m, report, s, p)
    for per, m, s, p in (extra or []):
        add(m, per, s, p)
    head = ['<context id="d0"><entity><identifier scheme="x">1</identifier></entity><period><instant>%s</instant></period></context>' % report]
    if dates:
        facts.append('<in-bse-shp:DateOfReport contextRef="d0">%s</in-bse-shp:DateOfReport>' % report)
    return ('<?xml version="1.0"?><xbrl %s>%s%s%s</xbrl>' % (NS, "".join(head), "".join(ctx), "".join(facts))).encode()


def new_rows(k=1.0, mem="MutualFundsOrUti", gov="Governments"):
    """A consistent new-format quarter on 1000 shares: prom 50, FII 15, DII 10 (MF 6), non-inst 25 -> other_dii 4."""
    return [("ShareholdingPattern", 1000, 100 * k),
            ("ShareholdingOfPromoterAndPromoterGroup", 500, 50 * k), ("PublicShareholding", 500, 50 * k),
            ("InstitutionsDomestic", 100, 10 * k), (mem, 60, 6 * k),
            ("InstitutionsForeign", 150, 15 * k), ("NonInstitutions", 250, 25 * k), (gov, 0, 0 * k)]


def old_rows():
    """Old format on 1000 shares: prom 50, institutions 30 (FPI 15, MF 6, other institutions 9 -> other_dii 9), non-inst 20."""
    return [("ShareholdingPattern", 1000, 100), ("ShareholdingOfPromoterAndPromoterGroup", 500, 50), ("PublicShareholding", 500, 50),
            ("Institutions", 300, 30), ("InstitutionsForeignPortfolioInvestor", 150, 15), ("MutualFundsOrUTI", 60, 6),
            ("FinancialInstitutionOrBanks", 30, 3), ("OtherInstitutions", 60, 6), ("NonInstitutions", 200, 20), ("Goverments", 0, 0)]


def rowmap(rows, report="2025-12-31"):
    return {n: r for (n, per), r in su.parse_xbrl(make_xbrl(rows, report))["rows"].items() if per == report}


def index_rec(date, rid, xbrl="https://nsearchives.nseindia.com/corporate/x_%s.xml", broadcast="10-JAN-2026 10:00:00", revised=False, **kw):
    r = {"date": date, "recordId": rid, "xbrl": (xbrl % rid if "%s" in xbrl else xbrl) if xbrl else None, "broadcastDate": broadcast, "submissionDate": "09-JAN-2026",
         "isin": "INE000A01010", "name": "Test Co", "revisedStatus": "N", "revisedData": "-", "revisionDate": "-", "revisionRemark": "-"}
    if revised:
        r.update(revisedStatus="Revised", revisedData="Revised", revisionDate="20-FEB-2026", revisionRemark="Re-filed with a correction")
    r.update(kw)
    return r


class FakeNse:
    def __init__(self, records=None, files=None, index_err=None):
        self.records, self.files, self.index_err, self.downloads, self.urls = records or {}, files or {}, index_err, 0, []

    def index(self, symbol):
        if self.index_err:
            return None, self.index_err
        return list(self.records.get(symbol, [])), None

    def xbrl(self, url):
        self.downloads += 1
        self.urls.append(url)
        v = self.files.get(url)
        if v is None:
            return None, "HTTP 404"
        if isinstance(v, str):
            return None, v
        return v, None


class Parse(unittest.TestCase):
    def test_rows_and_report_date(self):
        p = su.parse_xbrl(make_xbrl(new_rows(), "2025-12-31"))
        self.assertEqual(p["report_date"], "2025-12-31")
        self.assertEqual(p["rows"][("institutionsforeign", "2025-12-31")]["pct"], 15.0)
        self.assertEqual(p["rows"][("institutionsforeign", "2025-12-31")]["shares"], 150.0)
        self.assertEqual(p["duplicates"], [])

    def test_name_variants_are_one_category(self):
        for mem in ("MutualFundsOrUti", "MutualFundsOrUTI", "MutualFundsOrUTIMember"):
            self.assertIn("mutualfundsoruti", rowmap(new_rows(mem=mem)))
        self.assertEqual(su.norm_name("in-bse-shp:Goverments"), "goverments")

    def test_detail_members_and_other_dimensions_ignored(self):
        rows = new_rows() + [("InstitutionsForeign_Context01", 999, 99), ("DetailsOfShareholders", 5, 5)]
        rm = rowmap(rows)
        self.assertNotIn("institutionsforeigncontext01", rm)
        self.assertEqual(len([k for k in rm if k.startswith("details")]), 0)
        self.assertEqual(rm["institutionsforeign"]["pct"], 15.0)

    def test_other_period_contexts_do_not_leak(self):
        data = make_xbrl(new_rows(), "2026-06-30", extra=[("2026-06-23", "InstitutionsForeign", 1, 77)])
        p = su.parse_xbrl(data)
        self.assertEqual(p["rows"][("institutionsforeign", "2026-06-30")]["pct"], 15.0)
        self.assertEqual(p["rows"][("institutionsforeign", "2026-06-23")]["pct"], 77.0)

    def test_duplicate_conflict_is_reported(self):
        rows = new_rows() + [("InstitutionsForeign", 150, 16)]
        self.assertEqual(su.parse_xbrl(make_xbrl(rows))["duplicates"], ["InstitutionsForeign:pct"])

    def test_context_with_two_dimensions_is_ignored(self):
        data = make_xbrl(new_rows()).decode()
        extra = ('<context id="z1"><entity><identifier scheme="x">1</identifier><segment>'
                 '<xbrldi:explicitMember dimension="in-bse-shp:CategoryOfShareholdersAxis">in-bse-shp:InstitutionsForeign</xbrldi:explicitMember>'
                 '<xbrldi:explicitMember dimension="in-bse-shp:OtherAxis">in-bse-shp:Whatever</xbrldi:explicitMember></segment></entity>'
                 '<period><instant>2025-12-31</instant></period></context>'
                 '<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="z1">99</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>')
        p = su.parse_xbrl(data.replace("</xbrl>", extra + "</xbrl>").encode())
        self.assertEqual(p["rows"][("institutionsforeign", "2025-12-31")]["pct"], 15.0)
        self.assertEqual(p["duplicates"], [])

    def test_bad_xml_raises(self):
        with self.assertRaises(su.ET.ParseError):
            su.parse_xbrl(b"<not xml")

    def test_no_report_date_fact(self):
        self.assertIsNone(su.parse_xbrl(make_xbrl(new_rows(), dates=False))["report_date"])


class Mapping(unittest.TestCase):
    def test_new_format_percent(self):
        m = su.map_categories(rowmap(new_rows()))
        self.assertIsNone(m["problem"])
        self.assertEqual(m["values"], {"promoters": 50.0, "fii": 15.0, "other_dii": 4.0, "mutual_funds": 6.0, "retail_other": 25.0})
        self.assertEqual((m["format_version"], m["percent_unit"]), (su.FORMAT_NEW, "percent"))
        self.assertEqual(m["flags"], [])

    def test_new_format_fraction_is_normalised(self):
        rows = [(a, b, c) for a, b, c in new_rows(k=0.01)]
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["percent_unit"], "fraction")
        self.assertEqual(m["values"], {"promoters": 50.0, "fii": 15.0, "other_dii": 4.0, "mutual_funds": 6.0, "retail_other": 25.0})

    def test_old_format(self):
        m = su.map_categories(rowmap(old_rows()))
        self.assertEqual(m["format_version"], su.FORMAT_OLD)
        self.assertEqual(m["values"], {"promoters": 50.0, "fii": 15.0, "other_dii": 9.0, "mutual_funds": 6.0, "retail_other": 20.0})
        self.assertEqual([f["code"] for f in m["flags"]], [su.FLAG_OTHER_INST])

    def test_old_and_new_are_labelled_differently(self):
        self.assertNotEqual(su.map_categories(rowmap(old_rows()))["format_version"], su.map_categories(rowmap(new_rows()))["format_version"])

    def test_unit_is_never_guessed(self):
        rows = [("ShareholdingPattern", 1000, 55)] + new_rows()[1:]
        m = su.map_categories(rowmap(rows))
        self.assertIn("unit", m["problem"])
        self.assertNotIn("values", m)

    def test_unit_bounds(self):
        for total in (0.6, 0.98, 1.02, 98.9, 101.1):
            m = su.map_categories(rowmap([("ShareholdingPattern", 1000, total)] + new_rows()[1:]))
            self.assertIn("unit", m["problem"], total)
        for total, unit in ((0.99, "fraction"), (1.0, "fraction"), (1.01, "fraction"), (99.0, "percent"), (101.0, "percent")):
            self.assertEqual(su.detect_unit(rowmap([("ShareholdingPattern", 1000, total)])), unit, total)

    def test_unknown_structure(self):
        m = su.map_categories(rowmap([("ShareholdingPattern", 1000, 100), ("ShareholdingOfPromoterAndPromoterGroup", 500, 50)]))
        self.assertIn("not one of the two known formats", m["problem"])

    def test_trusts_separate_from_public_go_to_retail(self):
        rows = new_rows()
        rows = [(a, b, 24 if a == "NonInstitutions" else c) for a, b, c in rows] + [("EmployeeBenefitsTrusts", 10, 1)]
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["diagnostics"]["employee_trusts_mode"], "separate")
        self.assertEqual(m["values"]["retail_other"], 25.0)
        self.assertEqual(m["diagnostics"]["employee_trusts_pct"], 1.0)
        self.assertEqual(m["diagnostics"]["retail_other_excl_trusts"], 24.0)

    def test_trusts_nested_in_non_institutions_not_added_twice(self):
        rows = new_rows() + [("EmployeeBenefitsTrusts", 10, 1)]
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["diagnostics"]["employee_trusts_mode"], "nested")
        self.assertEqual(m["values"]["retail_other"], 25.0)

    def test_governments_join_retail(self):
        rows = [(a, b, 24 if a == "NonInstitutions" else (1 if a == "Governments" else c)) for a, b, c in new_rows()]
        self.assertEqual(su.map_categories(rowmap(rows))["values"]["retail_other"], 25.0)

    def test_promoter_row_absent_is_zero_only_when_public_holds_all(self):
        rows = [("ShareholdingPattern", 1000, 100), ("PublicShareholding", 1000, 100), ("InstitutionsDomestic", 300, 30), ("MutualFundsOrUti", 100, 10),
                ("InstitutionsForeign", 400, 40), ("NonInstitutions", 300, 30)]
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["values"]["promoters"], 0.0)
        self.assertTrue(m["diagnostics"]["promoter_row_absent"])
        rows2 = [r for r in new_rows() if r[0] != "ShareholdingOfPromoterAndPromoterGroup"]
        m2 = su.map_categories(rowmap(rows2))
        self.assertIsNone(m2["values"]["promoters"])
        self.assertIn(su.FLAG_ROW_MISSING, [f["code"] for f in m2["flags"]])

    def test_missing_row_never_becomes_zero(self):
        m = su.map_categories(rowmap([r for r in new_rows() if r[0] != "InstitutionsForeign"]))
        self.assertIsNone(m["values"]["fii"])

    def test_total_is_never_forced_to_100(self):
        rows = [(a, b, 30 if a == "NonInstitutions" else c) for a, b, c in new_rows()]
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["values"]["retail_other"], 30.0)
        self.assertEqual(m["diagnostics"]["total_reported_pct"], 105.0)
        self.assertIn(su.FLAG_SUM, [f["code"] for f in m["flags"]])

    def test_consistent_filing_is_not_flagged(self):
        self.assertEqual(su.map_categories(rowmap(new_rows()))["diagnostics"]["conflict"], None)

    def test_depository_receipt_shares_leave_the_share_basis(self):
        rows = new_rows() + [("CustodianOrDRHolder", 100, None)]
        rows[0] = ("ShareholdingPattern", 1100, 100)
        m = su.map_categories(rowmap(rows))
        self.assertEqual(m["diagnostics"]["share_basis_total"], 1000.0)
        self.assertIsNone(m["diagnostics"]["conflict"])


def icici_rows(reported, shares, report):
    """Promoter-less new-format filing; reported = (fii, mf, other_dii, retail) percent, shares = (fii, mf, domestic, noninst) on 10000 shares."""
    f, mf, od, rt = reported
    fs, ms, ds, ns = shares
    return [("ShareholdingPattern", 10000, 100), ("PublicShareholding", 10000, 100), ("InstitutionsForeign", fs, f), ("InstitutionsDomestic", ds, round(mf + od, 2)),
            ("MutualFundsOrUti", ms, mf), ("NonInstitutions", ns, rt)]


class IciciConflict(unittest.TestCase):
    def test_mar_2026_reported_values_kept_and_flagged(self):
        rows = icici_rows((34.49, 27.83, 12.12, 25.57), (4206, 3394, 4871, 924), "2026-03-31")
        rec = su.build_record(index_rec("31-MAR-2026", "7"), dt.date(2026, 3, 31), make_xbrl(rows, "2026-03-31"), None, None, "2026-10-05")
        self.assertEqual(rec["status"], "available")
        self.assertEqual(rec["values"], {"promoters": 0.0, "fii": 34.49, "other_dii": 12.12, "mutual_funds": 27.83, "retail_other": 25.57})
        self.assertEqual(rec["quality"]["status"], "flagged")
        self.assertIn(su.FLAG_CONFLICT, [f["code"] for f in rec["quality"]["flags"]])
        d = rec["diagnostics"]["share_derived_pct"]
        self.assertEqual((d["fii"], d["mutual_funds"], d["other_dii"], d["retail_other"]), (42.06, 33.94, 14.77, 9.24))
        self.assertNotEqual(rec["values"]["fii"], d["fii"])
        self.assertGreater(rec["diagnostics"]["conflict"]["max_abs_diff_pp"], 1.0)

    def test_jun_2026_flagged(self):
        rows = icici_rows((49.82, 20.0, 22.43, 7.65), (4024, 2500, 4000, 911), "2026-06-30")
        rec = su.build_record(index_rec("30-JUN-2026", "8"), dt.date(2026, 6, 30), make_xbrl(rows, "2026-06-30"), None, None, "2026-10-05")
        self.assertEqual(rec["values"]["fii"], 49.82)
        self.assertIn(su.FLAG_CONFLICT, [f["code"] for f in rec["quality"]["flags"]])

    def test_dec_2025_consistent_not_flagged(self):
        rows = icici_rows((43.87, 20.0, 11.0, 25.13), (4387, 2000, 3100, 2513), "2025-12-31")
        rec = su.build_record(index_rec("31-DEC-2025", "6"), dt.date(2025, 12, 31), make_xbrl(rows, "2025-12-31"), None, None, "2026-10-05")
        self.assertEqual(rec["quality"], {"status": "ok", "flags": []})
        self.assertEqual(rec["values"]["fii"], 43.87)


class BuildRecord(unittest.TestCase):
    def test_available_record_has_everything_for_audit(self):
        rec = su.build_record(index_rec("31-DEC-2025", "55", revised=True), dt.date(2025, 12, 31), make_xbrl(new_rows()), None, None, "2026-10-05")
        s = rec["source"]
        self.assertEqual((s["provider"], s["record_id"], s["revised"], s["revision_date"], s["revision_remark"]), ("NSE", "55", True, "2026-02-20", "Re-filed with a correction"))
        self.assertEqual((s["broadcast_date"], s["submission_date"]), ("2026-01-10", "2026-01-09"))
        self.assertTrue(s["xbrl_url"].startswith("https://"))
        self.assertEqual(rec["format_version"], su.FORMAT_NEW)
        self.assertEqual(rec["raw"]["total_shares"], 1000.0)
        self.assertEqual(rec["raw"]["rows"]["InstitutionsForeign"], [150.0, 15.0])
        self.assertEqual(rec["quality"], {"status": "ok", "flags": []})

    def test_not_revised_has_no_revision_fields(self):
        s = su.build_record(index_rec("31-DEC-2025", "55"), dt.date(2025, 12, 31), make_xbrl(new_rows()), None, None, "d")["source"]
        self.assertEqual((s["revised"], s["revision_date"], s["revision_remark"]), (False, None, None))

    def test_report_date_picks_the_right_rows(self):
        data = make_xbrl(new_rows(), "2026-06-30", extra=[("2026-06-23", "InstitutionsForeign", 1, 77)])
        rec = su.build_record(index_rec("30-JUN-2026", "9"), dt.date(2026, 6, 30), data, None, None, "d")
        self.assertEqual(rec["values"]["fii"], 15.0)

    def test_report_date_mismatch_flagged(self):
        rec = su.build_record(index_rec("31-DEC-2025", "9"), dt.date(2025, 12, 31), make_xbrl(new_rows(), "2025-12-30"), None, None, "d")
        self.assertIn(su.FLAG_REPORT_DATE, [f["code"] for f in rec["quality"]["flags"]])

    def test_missing_xbrl_is_unavailable_not_invented(self):
        rec = su.build_record(index_rec("30-SEP-2021", "3"), dt.date(2021, 9, 30), None, None, "HTTP 404", "d")
        self.assertEqual(rec["status"], "unavailable")
        self.assertEqual(rec["values"], su.empty_values())
        self.assertIn("HTTP 404", rec["reason"])
        self.assertEqual(rec["quality"]["flags"][0]["code"], "xbrl-unavailable")
        self.assertEqual(rec["source"]["record_id"], "3")

    def test_unparsable_and_no_rows(self):
        a = su.build_record(index_rec("31-DEC-2025", "1"), dt.date(2025, 12, 31), b"<broken", None, None, "d")
        b = su.build_record(index_rec("31-DEC-2025", "1"), dt.date(2025, 12, 31), make_xbrl(new_rows(), "2025-12-31").replace(b">2025-12-31</in-bse-shp:DateOfReport>", b">2020-01-01</in-bse-shp:DateOfReport>"), None, None, "d")
        c = su.build_record(index_rec("31-DEC-2025", "1"), dt.date(2025, 12, 31), make_xbrl([("Foo", 1, 1)]), None, None, "d")
        for r in (a, b, c):
            self.assertEqual(r["status"], "unavailable")
            self.assertTrue(r["reason"])
        self.assertEqual(a["quality"]["flags"][0]["code"], "xbrl-unparsable")

    def test_url_used_differs_from_listed(self):
        rec = su.build_record(index_rec("31-DEC-2025", "1"), dt.date(2025, 12, 31), make_xbrl(new_rows()), "https://archives.nseindia.com/a.xml", None, "d")
        self.assertEqual(rec["source"]["xbrl_url"], "https://archives.nseindia.com/a.xml")
        self.assertIn("nsearchives", rec["source"]["xbrl_url_listed"])


class Index(unittest.TestCase):
    def test_selection(self):
        recs = [index_rec("31-DEC-2025", "1"), index_rec("08-DEC-2021", "2"), index_rec("30-JUN-2021", "3"), index_rec("30-SEP-2021", "4"),
                index_rec("junk", "5"), index_rec("29-OCT-2024", "6")]
        keep, skipped = su.select_filings(recs)
        self.assertEqual(sorted(d.isoformat() for d in keep), ["2021-09-30", "2025-12-31"])
        reasons = sorted(s["reason"] for s in skipped)
        self.assertEqual(sum("off-cycle" in r for r in reasons), 2)
        self.assertEqual(sum("before Sep 2021" in r for r in reasons), 1)
        self.assertEqual(sum("unparsable" in r for r in reasons), 1)

    def test_latest_broadcast_wins_duplicate(self):
        a = index_rec("31-DEC-2025", "10", broadcast="10-JAN-2026 10:00:00")
        b = index_rec("31-DEC-2025", "11", broadcast="15-FEB-2026 09:00:00")
        for order in ([a, b], [b, a]):
            keep, skipped = su.select_filings(order)
            self.assertEqual(keep[dt.date(2025, 12, 31)]["recordId"], "11")
            self.assertEqual(skipped[0]["record_id"], "10")

    def test_expected_quarters(self):
        q = su.expected_quarters(dt.date(2026, 6, 30))
        self.assertEqual((len(q), q[0], q[-1]), (20, "2021-09-30", "2026-06-30"))


class HostFallback(unittest.TestCase):
    def test_candidates(self):
        c = su.xbrl_candidates("https://nsearchives.nseindia.com/a/b.xml")
        self.assertEqual(c, ["https://nsearchives.nseindia.com/a/b.xml", "https://archives.nseindia.com/a/b.xml", "https://www.nseindia.com/a/b.xml"])
        self.assertEqual(su.xbrl_candidates("https://example.com/a.xml"), ["https://example.com/a.xml"])

    def test_404_tries_other_official_hosts_and_records_the_url_used(self):
        nse = FakeNse(files={"https://archives.nseindia.com/a/b.xml": b"<x/>"})
        data, used, err = su.fetch_xbrl(nse, "https://nsearchives.nseindia.com/a/b.xml")
        self.assertEqual((data, used, err), (b"<x/>", "https://archives.nseindia.com/a/b.xml", None))

    def test_other_errors_are_not_retried_elsewhere(self):
        nse = FakeNse(files={"https://nsearchives.nseindia.com/a/b.xml": "HTTP 500"})
        data, used, err = su.fetch_xbrl(nse, "https://nsearchives.nseindia.com/a/b.xml")
        self.assertEqual((data, used, err, len(nse.urls)), (None, None, "HTTP 500", 1))

    def test_all_404(self):
        nse = FakeNse()
        self.assertEqual(su.fetch_xbrl(nse, "https://nsearchives.nseindia.com/a/b.xml")[2], "HTTP 404")
        self.assertEqual(len(nse.urls), 3)


def stock_of(ledger, sym="ITC"):
    return ledger["stocks"][sym]


def quarters(ledger, sym="ITC"):
    return {r["quarter_end"]: r for r in ledger["stocks"][sym]["quarters"]}


class Process(unittest.TestCase):
    def setUp(self):
        self.url = lambda rid: "https://nsearchives.nseindia.com/corporate/x_%s.xml" % rid

    def run_once(self, nse, ledger=None, refresh=False, budget=100):
        ledger = ledger if ledger is not None else sl.new_ledger()
        b = [budget]
        ev, skipped, err = su.process_stock(nse, ledger, "ITC", "2026-10-05", b, refresh)
        return ledger, ev, skipped, err

    def test_itc_missing_xbrl_is_stored_unavailable_then_resurrected(self):
        recs = [index_rec("30-SEP-2021", "1"), index_rec("31-DEC-2021", "2"), index_rec("31-MAR-2022", "3")]
        files = {self.url("3"): make_xbrl(old_rows(), "2022-03-31")}
        ledger, ev, _, err = self.run_once(FakeNse({"ITC": recs}, files))
        self.assertIsNone(err)
        q = quarters(ledger)
        self.assertEqual((q["2021-09-30"]["status"], q["2021-12-31"]["status"], q["2022-03-31"]["status"]), ("unavailable", "unavailable", "available"))
        self.assertEqual(q["2021-09-30"]["values"], su.empty_values())
        self.assertEqual(ev["2021-09-30"], "added")
        # the same run again: the unavailable quarters are retried, and the available one is not downloaded again
        nse2 = FakeNse({"ITC": recs}, files)
        ledger, ev2, _, _ = self.run_once(nse2, ledger)
        self.assertEqual(ev2["2022-03-31"], "unchanged")
        self.assertEqual(ev2["2021-09-30"], "unchanged")
        self.assertFalse(any(u.endswith("x_3.xml") for u in nse2.urls))
        # the file appears later
        files[self.url("1")] = make_xbrl(old_rows(), "2021-09-30")
        ledger, ev3, _, _ = self.run_once(FakeNse({"ITC": recs}, files), ledger)
        self.assertEqual(ev3["2021-09-30"], "resurrected")
        q = quarters(ledger)
        self.assertEqual(q["2021-09-30"]["status"], "available")
        self.assertEqual(q["2021-09-30"]["revisions"][0]["status"], "unavailable")

    def test_twenty_quarters_newest_first(self):
        qs = su.expected_quarters(dt.date(2026, 6, 30))
        recs, files = [], {}
        for i, q in enumerate(qs):
            d = dt.date.fromisoformat(q)
            lab = d.strftime("%d-%b-%Y").upper()
            recs.append(index_rec(lab, str(100 + i)))
            files[self.url(100 + i)] = make_xbrl(old_rows() if d < dt.date(2022, 9, 30) else new_rows(), q)
        ledger, ev, _, err = self.run_once(FakeNse({"ITC": recs}, files))
        qq = ledger["stocks"]["ITC"]["quarters"]
        self.assertEqual(len(qq), 20)
        self.assertEqual([r["quarter_end"] for r in qq], sorted(qs, reverse=True))
        fmts = {r["quarter_end"]: r["format_version"] for r in qq}
        self.assertEqual(fmts["2022-06-30"], su.FORMAT_OLD)
        self.assertEqual(fmts["2022-09-30"], su.FORMAT_NEW)
        self.assertTrue(all(r["format_version"] for r in qq))

    def test_a_quarter_the_index_does_not_list_is_never_invented(self):
        recs = [index_rec("31-DEC-2025", "1"), index_rec("30-SEP-2025", "2")]
        files = {self.url("1"): make_xbrl(new_rows()), self.url("2"): make_xbrl(new_rows(), "2025-09-30")}
        ledger, *_ = self.run_once(FakeNse({"ITC": recs}, files))
        self.assertEqual(set(quarters(ledger)), {"2025-12-31", "2025-09-30"})
        doc = su.build_output(ledger, {}, "d")
        miss = doc["stocks"]["ITC"]["coverage"]["missing_quarters"]
        self.assertEqual((len(miss), "2025-12-31" in miss, "2025-09-30" in miss, "2025-06-30" in miss), (16, False, False, True))
        # a gap in the middle is reported as missing, not filled
        recs2 = [index_rec("31-DEC-2025", "1"), index_rec("31-MAR-2025", "5")]
        files2 = {self.url("1"): make_xbrl(new_rows()), self.url("5"): make_xbrl(new_rows(), "2025-03-31")}
        ledger2, *_ = self.run_once(FakeNse({"ITC": recs2}, files2))
        miss2 = su.build_output(ledger2, {}, "d")["stocks"]["ITC"]["coverage"]["missing_quarters"]
        self.assertTrue({"2025-06-30", "2025-09-30"} <= set(miss2) and not {"2025-03-31", "2025-12-31"} & set(miss2))
        self.assertNotIn("2025-06-30", quarters(ledger2))

    def test_index_errors(self):
        for nse, text in ((FakeNse(index_err="HTTP 403"), "HTTP 403"), (FakeNse({"ITC": []}), "empty"), (FakeNse({"ITC": [index_rec("08-DEC-2021", "1")]}), "no quarter-end")):
            ledger, ev, _, err = self.run_once(nse)
            self.assertIn(text, err)
            self.assertEqual(ledger["stocks"], {})

    def test_revision_new_record_id_is_kept_as_history(self):
        files = {self.url("1"): make_xbrl(new_rows())}
        ledger, *_ = self.run_once(FakeNse({"ITC": [index_rec("31-DEC-2025", "1")]}, files))
        rows2 = [(a, b, 24 if a == "NonInstitutions" else (1 if a == "Governments" else c)) for a, b, c in new_rows()]
        rows3 = [(a, b, 49 if a == "ShareholdingOfPromoterAndPromoterGroup" else c) for a, b, c in rows2]
        files[self.url("2")] = make_xbrl(rows3)
        rec2 = index_rec("31-DEC-2025", "2", broadcast="20-FEB-2026 10:00:00", revised=True)
        ledger, ev, *_ = self.run_once(FakeNse({"ITC": [rec2]}, files), ledger)
        self.assertEqual(ev["2025-12-31"], "revised")
        r = quarters(ledger)["2025-12-31"]
        self.assertEqual(r["source"]["record_id"], "2")
        self.assertTrue(r["source"]["revised"])
        self.assertEqual(r["values"]["promoters"], 49.0)
        self.assertEqual(len(r["revisions"]), 1)
        self.assertEqual((r["revisions"][0]["values"]["promoters"], r["revisions"][0]["source"]["record_id"]), (50.0, "1"))
        self.assertEqual(sl.no_loss_problems({"schema": 1, "stocks": {"ITC": {"quarters": [dict(r, values=dict(r["values"], promoters=50.0), source=r["revisions"][0]["source"], revisions=[])]}}}, ledger), [])

    def test_same_record_id_is_not_downloaded_again_unless_refresh_all(self):
        files = {self.url("1"): make_xbrl(new_rows())}
        recs = [index_rec("31-DEC-2025", "1")]
        ledger, *_ = self.run_once(FakeNse({"ITC": recs}, files))
        n = FakeNse({"ITC": recs}, files)
        self.run_once(n, ledger)
        self.assertEqual(n.downloads, 0)
        n = FakeNse({"ITC": recs}, files)
        _, ev, *_ = self.run_once(n, ledger, refresh=True)
        self.assertEqual((n.downloads, ev["2025-12-31"]), (1, "unchanged"))

    def test_failed_refetch_never_erases_an_available_quarter(self):
        files = {self.url("1"): make_xbrl(new_rows())}
        ledger, *_ = self.run_once(FakeNse({"ITC": [index_rec("31-DEC-2025", "1")]}, files))
        before = json.dumps(ledger, sort_keys=True)
        ledger, ev, *_ = self.run_once(FakeNse({"ITC": [index_rec("31-DEC-2025", "9")]}, {}), ledger)
        self.assertEqual(ev["2025-12-31"], "kept-existing")
        self.assertEqual(json.dumps(ledger, sort_keys=True), before)

    def test_download_budget(self):
        recs = [index_rec("31-DEC-2025", "1"), index_rec("30-SEP-2025", "2")]
        files = {self.url("1"): make_xbrl(new_rows()), self.url("2"): make_xbrl(new_rows(), "2025-09-30")}
        ledger, ev, *_ = self.run_once(FakeNse({"ITC": recs}, files), budget=1)
        self.assertEqual(ev, {"2025-12-31": "added", "2025-09-30": "not-attempted"})
        self.assertEqual(set(quarters(ledger)), {"2025-12-31"})

    def test_listed_no_xbrl(self):
        ledger, ev, *_ = self.run_once(FakeNse({"ITC": [index_rec("31-DEC-2025", "1", xbrl=None)]}))
        r = quarters(ledger)["2025-12-31"]
        self.assertEqual(r["status"], "unavailable")
        self.assertIn("no XBRL", r["reason"])


class UpstoxIsins(unittest.TestCase):
    def test_list_from_the_instrument_file_and_failure_is_not_fatal(self):
        with mock.patch.object(su, "load_instruments", lambda: {"INFY": {"isin": "INE009A01021", "name": "x"}, "BAD": {"isin": None}}):
            self.assertEqual(su.upstox_isins(), {"INFY": "INE009A01021"})
        def boom():
            raise SystemExit(1)
        with mock.patch.object(su, "load_instruments", boom):
            self.assertEqual(su.upstox_isins(), {})

    def test_isin_comes_from_any_index_record(self):
        recs = [index_rec("30-JUN-2026", "1", isin=None), index_rec("31-MAR-2026", "2", isin="INE002A01018")]
        led = sl.new_ledger()
        su.process_stock(FakeNse({"RELIANCE": recs}, {}), led, "RELIANCE", "d", [0])
        self.assertEqual(led["stocks"]["RELIANCE"]["isin"], "INE002A01018")
        led2 = sl.new_ledger()
        su.process_stock(FakeNse({"RELIANCE": [index_rec("30-JUN-2026", "1", isin=None)]}, {}), led2, "RELIANCE", "d", [0])
        self.assertIsNone(led2["stocks"]["RELIANCE"]["isin"])


class Merge(unittest.TestCase):
    def rec(self, **kw):
        r = su.build_record(index_rec("31-DEC-2025", "1"), dt.date(2025, 12, 31), make_xbrl(new_rows()), None, None, "2026-01-01")
        r.update(kw)
        return r

    def test_added_unchanged(self):
        new = self.rec()
        self.assertEqual(su.merge_record(None, new, "d"), (new, "added"))
        old = self.rec()
        self.assertEqual(su.merge_record(old, self.rec(), "d")[1], "unchanged")

    def test_value_never_becomes_null(self):
        old = self.rec()
        new = self.rec()
        new["values"]["mutual_funds"] = None
        new["source"] = dict(new["source"], record_id="2")
        merged, ev = su.merge_record(old, new, "2026-10-05")
        self.assertEqual(merged["values"]["mutual_funds"], 6.0)
        self.assertIn(su.FLAG_KEPT, [f["code"] for f in merged["quality"]["flags"]])
        self.assertEqual(ev, "revised")

    def test_same_values_and_id_but_new_explanation_is_a_refresh(self):
        old = self.rec()
        new = self.rec()
        new["diagnostics"] = dict(new["diagnostics"], employee_trusts_mode="x")
        merged, ev = su.merge_record(old, new, "d")
        self.assertEqual((ev, merged["revisions"], merged["fetched"]), ("refreshed", [], "2026-01-01"))

    def test_unavailable_stays_unavailable_with_history(self):
        a = su.build_record(index_rec("30-SEP-2021", "1"), dt.date(2021, 9, 30), None, None, "HTTP 404", "d1")
        b = su.build_record(index_rec("30-SEP-2021", "1"), dt.date(2021, 9, 30), None, None, "HTTP 404", "d2")
        self.assertEqual(su.merge_record(a, b, "d2")[1], "unchanged")
        c = su.build_record(index_rec("30-SEP-2021", "1"), dt.date(2021, 9, 30), None, None, "HTTP 500", "d3")
        self.assertEqual(su.merge_record(a, c, "d3")[1], "refreshed")


class CrossCheck(unittest.TestCase):
    def stock(self):
        r = su.build_record(index_rec("30-JUN-2026", "1"), dt.date(2026, 6, 30), make_xbrl(new_rows(), "2026-06-30"), None, None, "d")
        return {"symbol": "ITC", "isin": "INE154A01025", "quarters": [r]}

    def api(self, vals, period="Jun 2026"):
        a = mock.Mock()
        a.get.return_value = ({"data": [{"category": k, "history": [{"period": period, "value": v}]} for k, v in vals.items()]}, None)
        return a

    GOOD = {"promoters": 50.0, "fii": 15.0, "other_dii": 4.0, "mutual_funds": 6.0, "retail_and_other": 25.0}

    def test_match(self):
        r = su.cross_check(self.api(self.GOOD), self.stock())
        self.assertEqual((r["status"], r["max_abs_diff"]), ("match", 0.0))

    def test_mismatch_is_reported_and_never_changes_values(self):
        st = self.stock()
        before = json.dumps(st, sort_keys=True)
        r = su.cross_check(self.api(dict(self.GOOD, fii=20.0)), st)
        self.assertEqual((r["status"], r["categories"]["fii"]["diff"]), ("mismatch", -5.0))
        self.assertEqual(json.dumps(st, sort_keys=True), before)

    def test_skipped_without_token_unavailable_on_error_or_other_quarter(self):
        self.assertEqual(su.cross_check(None, self.stock())["status"], "skipped")
        a = mock.Mock()
        a.get.return_value = (None, "HTTP 500")
        r = su.cross_check(a, self.stock(), "INE154A01025")
        self.assertEqual((r["status"], r["upstox_isin"]), ("unavailable", "INE154A01025"))
        self.assertEqual(su.cross_check(self.api(self.GOOD, "Mar 2026"), self.stock(), "INE154A01025")["upstox_isin"], "INE154A01025")
        self.assertEqual(su.cross_check(self.api(self.GOOD, "Mar 2026"), self.stock())["status"], "unavailable")
        self.assertEqual(su.cross_check(None, {"symbol": "X", "isin": None, "quarters": []})["status"], "unavailable")

    def test_the_upstox_isin_is_asked_for_not_the_nse_one(self):
        st = self.stock()
        st["isin"] = "IN9009A01011"                      # what NSE's filing index carried for INFY: Upstox does not know it
        a = self.api(self.GOOD)
        r = su.cross_check(a, st, "INE009A01021")
        a.get.assert_called_once_with("INE009A01021/share-holdings")
        self.assertEqual((r["status"], r["upstox_isin"]), ("match", "INE009A01021"))
        self.assertEqual(st["isin"], "IN9009A01011")      # the stored value is never replaced
        a2 = self.api(self.GOOD)
        su.cross_check(a2, st)
        a2.get.assert_called_once_with("IN9009A01011/share-holdings")      # without an Upstox ISIN the NSE one is the fallback

    def test_no_isin_anywhere_is_reported_not_guessed(self):
        st = self.stock()
        st["isin"] = None
        a = self.api(self.GOOD)
        r = su.cross_check(a, st)
        self.assertEqual(r["status"], "unavailable")
        self.assertIn("no ISIN", r["reason"])
        a.get.assert_not_called()
        self.assertEqual(su.cross_check(a, st, "INE002A01018")["status"], "match")      # RELIANCE: NSE gives none, Upstox's list does

    def test_the_upstox_label_for_every_quarter_end(self):
        for q, label in (("2025-03-31", "Mar 2025"), ("2025-06-30", "Jun 2025"), ("2025-09-30", "Sep 2025"), ("2025-12-31", "Dec 2025")):
            st = self.stock()
            st["quarters"][0]["quarter_end"] = q
            self.assertEqual(su.cross_check(self.api(self.GOOD, label), st)["status"], "match", label)

    def test_partial_upstox_categories_are_not_a_match(self):
        r = su.cross_check(self.api({"promoters": 50.0}), self.stock())
        self.assertEqual(r["status"], "mismatch")


class OutputAndMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        (self.t / "branch").mkdir()
        recs = {"ITC": [index_rec("31-DEC-2025", "1", isin="INE154A01017", name="ITC Ltd"), index_rec("30-SEP-2021", "2", isin="INE154A01017", name="ITC Ltd"),
                        index_rec("08-DEC-2021", "3", isin="INE154A01017", name="ITC Ltd")],
                "TCS": [index_rec("31-DEC-2025", "4", isin="INE467B01029", name="TCS")]}
        u = lambda r: "https://nsearchives.nseindia.com/corporate/x_%s.xml" % r
        files = {u("1"): make_xbrl(new_rows()), u("4"): make_xbrl(new_rows())}
        self.nse = FakeNse(recs, files)
        self.env = {"SHAREHOLDING_LEDGER_DIR": str(self.t / "branch"), "SHAREHOLDING_INIT": "true", "SHAREHOLDING_SYMBOLS": "ITC,TCS",
                    "SHAREHOLDING_LEDGER_OUT": str(self.t / "ledger-out" / "shareholding_ledger.json")}

    def run_main(self, env=None, nse=None, upstox=None, isins=None):
        e = dict(self.env, **(env or {}))
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.dict(os.environ, e, clear=False), mock.patch.object(su, "Nse", lambda: nse or self.nse),
                   mock.patch.object(su, "OUT_FILE", self.t / "out" / "shareholding.json"), mock.patch.object(su, "log", lambda *a: print(*a))]
        if upstox is not None:
            patches.append(mock.patch.object(su, "Upstox", upstox))
        patches.append(mock.patch.object(su, "upstox_isins", isins or (lambda: {"ITC": "INE154A01025", "TCS": "INE467B01029"})))
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            if "UPSTOX_ANALYTICS_TOKEN" not in e:
                os.environ.pop("UPSTOX_ANALYTICS_TOKEN", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = su.main()
        return rc, out.getvalue() + err.getvalue()

    def test_first_run_writes_ledger_and_output(self):
        rc, text = self.run_main()
        self.assertEqual(rc, 0, text)
        led = json.loads((self.t / "ledger-out" / "shareholding_ledger.json").read_text())
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertEqual(sorted(led["stocks"]), ["ITC", "TCS"])
        self.assertEqual(sl.verify_first(led), [])
        self.assertEqual(su.validate_doc(doc), [])
        self.assertEqual(doc["summary"], {"stocks": 2, "quarter_records": 3, "available": 2, "unavailable": 1, "flagged": 1})
        itc = doc["stocks"]["ITC"]
        self.assertEqual(itc["coverage"]["unavailable_quarters"], ["2021-09-30"])
        self.assertEqual([s["reason"] for s in itc["off_cycle_filings"]], ["off-cycle filing (not a quarter end)"])
        self.assertEqual(itc["cross_check"]["status"], "skipped")
        self.assertEqual(doc["categories"], su.CATEGORIES)
        self.assertNotIn("raw", itc["quarters"][0])

    def test_branch_missing_or_ledger_missing_is_an_error(self):
        rc, text = self.run_main({"SHAREHOLDING_LEDGER_DIR": ""})
        self.assertEqual(rc, 1)
        self.assertIn("no data branch", text)
        rc, text = self.run_main({"SHAREHOLDING_INIT": "false"})
        self.assertEqual(rc, 1)
        self.assertIn("holds no shareholding_ledger.json", text)
        self.assertFalse((self.t / "out").exists())

    def test_init_never_replaces_an_existing_ledger(self):
        (self.t / "branch" / sl.LEDGER_NAME).write_text(json.dumps(sl.new_ledger()))
        rc, text = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("very first run only", text)

    def test_second_run_reads_the_branch_ledger_and_downloads_nothing(self):
        self.assertEqual(self.run_main()[0], 0)
        (self.t / "branch" / sl.LEDGER_NAME).write_text((self.t / "ledger-out" / "shareholding_ledger.json").read_text())
        n = FakeNse(self.nse.records, self.nse.files)
        rc, text = self.run_main({"SHAREHOLDING_INIT": "false"}, nse=n)
        self.assertEqual(rc, 0, text)
        self.assertEqual(n.downloads, 3)          # only ITC's unavailable 2021 quarter is retried: the listed host plus the two other official NSE hosts
        self.assertNotIn("x_1.xml", " ".join(n.urls))

    def test_unknown_symbol_and_all_failed(self):
        rc, text = self.run_main({"SHAREHOLDING_SYMBOLS": "NOPE"})
        self.assertEqual(rc, 1)
        self.assertIn("Unknown symbols", text)
        rc, text = self.run_main(nse=FakeNse(index_err="HTTP 403"))
        self.assertEqual(rc, 1)
        self.assertFalse((self.t / "out").exists())

    def test_one_stock_failing_does_not_stop_the_others_and_is_reported(self):
        n = FakeNse({"ITC": self.nse.records["ITC"]}, self.nse.files)
        rc, text = self.run_main(nse=n)
        self.assertEqual(rc, 0, text)
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertNotIn("TCS", doc["stocks"])

    def test_token_is_never_printed_or_written(self):
        fake = mock.Mock()
        fake.return_value.get.return_value = (None, "HTTP 500")
        rc, text = self.run_main({"UPSTOX_ANALYTICS_TOKEN": SECRET}, upstox=fake)
        self.assertEqual(rc, 0, text)
        self.assertNotIn(SECRET, text)
        for f in self.t.rglob("*.json"):
            self.assertNotIn(SECRET, f.read_text())
        fake.assert_called_once_with(SECRET, 20)
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertEqual(doc["stocks"]["ITC"]["cross_check"]["status"], "unavailable")

    def test_main_asks_upstox_about_its_own_isin_and_never_stores_it(self):
        fake = mock.Mock()
        fake.return_value.get.return_value = ({"data": []}, None)
        rc, text = self.run_main({"UPSTOX_ANALYTICS_TOKEN": SECRET}, upstox=fake)
        self.assertEqual(rc, 0, text)
        paths = sorted(c.args[0] for c in fake.return_value.get.call_args_list)
        self.assertEqual(paths, ["INE154A01025/share-holdings", "INE467B01029/share-holdings"])
        led = json.loads((self.t / "ledger-out" / "shareholding_ledger.json").read_text())
        self.assertEqual(led["stocks"]["ITC"]["isin"], "INE154A01017")      # the NSE index value (as filed) stays in the ledger
        self.assertEqual(led["stocks"]["TCS"]["isin"], "INE467B01029")
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertEqual(doc["stocks"]["ITC"]["cross_check"]["upstox_isin"], "INE154A01025")

    def test_without_a_token_the_instrument_list_is_not_even_requested(self):
        called = mock.Mock(side_effect=AssertionError("must not be called"))
        rc, text = self.run_main(isins=called)
        self.assertEqual(rc, 0, text)
        called.assert_not_called()

    def test_rejected_token_does_not_lose_the_run(self):
        fake = mock.Mock()
        fake.return_value.get.side_effect = SystemExit(1)
        rc, text = self.run_main({"UPSTOX_ANALYTICS_TOKEN": SECRET}, upstox=fake)
        self.assertEqual(rc, 0, text)
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertEqual(doc["stocks"]["TCS"]["cross_check"]["status"], "unavailable")
        self.assertIn("earlier in this run", doc["stocks"]["TCS"]["cross_check"]["reason"])
        self.assertEqual(fake.return_value.get.call_count, 1)      # after a rejection no further request is sent

    def test_a_faulty_merge_that_loses_data_is_stopped_before_anything_is_written(self):
        self.assertEqual(self.run_main()[0], 0)
        (self.t / "branch" / sl.LEDGER_NAME).write_text((self.t / "ledger-out" / "shareholding_ledger.json").read_text())
        before = (self.t / "ledger-out" / "shareholding_ledger.json").read_text()
        (self.t / "out" / "shareholding.json").unlink()

        def faulty(old, new, day):
            if old is not None and old["status"] == "available":
                return dict(old, values=dict(old["values"], fii=None)), "revised"
            return su.merge_record.__wrapped__(old, new, day) if hasattr(su.merge_record, "__wrapped__") else (new, "added")
        n = FakeNse(self.nse.records, self.nse.files)
        with mock.patch.object(su, "merge_record", faulty):
            rc, text = self.run_main({"SHAREHOLDING_INIT": "false", "SHAREHOLDING_REFRESH": "all"}, nse=n)
        self.assertEqual(rc, 1)
        self.assertIn("a value was lost", text)
        self.assertFalse((self.t / "out" / "shareholding.json").exists())
        self.assertEqual((self.t / "ledger-out" / "shareholding_ledger.json").read_text(), before)

    def test_no_loss_violation_stops_before_writing(self):
        self.assertEqual(self.run_main()[0], 0)
        led = json.loads((self.t / "ledger-out" / "shareholding_ledger.json").read_text())
        led["stocks"]["ITC"]["quarters"].append(dict(led["stocks"]["ITC"]["quarters"][0], quarter_end="2025-09-30"))   # a quarter the index will not return
        (self.t / "branch" / sl.LEDGER_NAME).write_text(json.dumps(led))
        (self.t / "out" / "shareholding.json").unlink()
        rc, text = self.run_main({"SHAREHOLDING_INIT": "false"})
        self.assertEqual(rc, 0, text)      # an old quarter the index no longer lists is kept, not removed
        doc = json.loads((self.t / "out" / "shareholding.json").read_text())
        self.assertEqual(len(doc["stocks"]["ITC"]["quarters"]), 3)


class ValidateDoc(unittest.TestCase):
    def good(self):
        led = sl.new_ledger()
        nse = FakeNse({"ITC": [index_rec("31-DEC-2025", "1"), index_rec("30-SEP-2021", "2")]}, {"https://nsearchives.nseindia.com/corporate/x_1.xml": make_xbrl(new_rows())})
        su.process_stock(nse, led, "ITC", "d", [10])
        return su.build_output(led, {}, "d")

    def test_good(self):
        self.assertEqual(su.validate_doc(self.good()), [])

    def mutate(self, f):
        d = self.good()
        f(d["stocks"]["ITC"]["quarters"])
        return su.validate_doc(d)

    def test_catches(self):
        self.assertTrue(self.mutate(lambda q: q[0]["values"].update(fii=150)))
        self.assertTrue(self.mutate(lambda q: q[0].update(status="x")))
        self.assertTrue(self.mutate(lambda q: q[0].update(format_version=None)))
        self.assertTrue(self.mutate(lambda q: q[0]["source"].update(record_id=None)))
        self.assertTrue(self.mutate(lambda q: q[1]["values"].update(fii=1.0)))
        self.assertTrue(self.mutate(lambda q: q[1].update(reason=None)))
        self.assertTrue(self.mutate(lambda q: q.reverse()))
        self.assertTrue(self.mutate(lambda q: q[0]["quality"].update(status="flagged")))
        self.assertTrue(self.mutate(lambda q: q[0]["values"].update(fii=None)))
        self.assertTrue(self.mutate(lambda q: q[0].update(quarter_end="2025-12-30")))
        self.assertTrue(self.mutate(lambda q: q.append(dict(q[0]))))
        self.assertEqual(su.validate_doc({}), ["wrong shape or schema"])

    def test_null_with_flag_is_allowed(self):
        def f(q):
            q[0]["values"]["fii"] = None
            q[0]["quality"] = {"status": "flagged", "flags": [{"code": su.FLAG_ROW_MISSING, "detail": "fii"}]}
        self.assertEqual(self.mutate(f), [])


class Http(unittest.TestCase):
    def test_nse_index_shapes_and_404_is_final(self):
        class R:
            def __init__(s, code, body=None):
                s.status_code, s.body, s.content = code, body, b"x"

            def json(s):
                if s.body is None:
                    raise ValueError
                return s.body

        sess = mock.Mock()
        sess.get.return_value = R(200, {"data": [{"a": 1}, 3]})
        n = su.Nse(sess, wait=0)
        self.assertEqual(n.index("ITC"), ([{"a": 1}], None))
        sess.get.return_value = R(200, [{"a": 2}])
        self.assertEqual(su.Nse(sess, wait=0).index("ITC"), ([{"a": 2}], None))
        sess.get.return_value = R(200, None)
        self.assertEqual(su.Nse(sess, wait=0).index("ITC")[1], "the filing index was not JSON")
        sess.get.return_value = R(200, {"x": 1})
        self.assertIn("unexpected shape", su.Nse(sess, wait=0).index("ITC")[1])
        sess.get.reset_mock()
        sess.get.return_value = R(404)
        self.assertEqual(su.Nse(sess, wait=0).xbrl("https://x/a.xml"), (None, "HTTP 404"))
        self.assertEqual(sess.get.call_count, 1)
        sess.get.reset_mock()
        sess.get.return_value = R(403)
        with mock.patch.object(su.requests, "Session") as S:
            S.return_value = sess
            n = su.Nse(sess, wait=0)
            self.assertEqual(n.xbrl("https://x/a.xml"), (None, "HTTP 403"))
        self.assertEqual(sum(1 for c in sess.get.call_args_list if c.args[0] == "https://x/a.xml"), 3)


class Hygiene(unittest.TestCase):
    def test_nse_updater_not_imported_and_no_token_leak_paths(self):
        src = Path(su.__file__).read_text()
        self.assertNotIn("import nse_updater", src)
        self.assertNotIn("from nse_updater", src)
        self.assertEqual(sorted(l for l in src.splitlines() if "UPSTOX_ANALYTICS_TOKEN" in l and "print" in l), [])

    def test_the_ten_stocks(self):
        self.assertEqual(sorted(su.SYMBOLS), sorted("RELIANCE TCS INFY HDFCBANK ICICIBANK SBIN ITC BHARTIARTL LT MARUTI".split()))


if __name__ == "__main__":
    unittest.main()
