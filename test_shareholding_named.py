"""
test_shareholding_named.py - Phase 5H.6 tests for the named-holder (major disclosed shareholders) data layer. Standard unittest, no network.
Holder XBRL is synthetic, built with the typed-member structure the Phase 5H.6 probe read. No shareholder name below is known to the code.
"""
import copy
import re
import unittest

import shareholding_ledger as sl
import shareholding_updater as su
from test_shareholding import FakeNse, index_rec, make_xbrl, new_rows, old_rows

REPORT = "2025-12-31"


def with_holders(base, holders, report=REPORT):
    """holders: [dict(axis, n, name, shares, pct, flag=None, name_ctx=True)]. Adds the descriptive + numeric context pair per holder (typed member)."""
    ctx, facts = [], []
    for h in holders:
        member = "%s_Context%02d" % (h["cat"], h["n"])
        ax = "in-bse-shp:%s" % h["axis"]
        for cid in ("D_" + member, member):
            ctx.append('<context id="%s"><entity><identifier scheme="x">1</identifier><segment><xbrldi:typedMember dimension="%s"><in-bse-shp:%s>%s</in-bse-shp:%s></xbrldi:typedMember></segment></entity>'
                       '<period><instant>%s</instant></period></context>' % (cid, ax, h.get("dom", "DomainMember"), member, h.get("dom", "DomainMember"), h.get("period", report)))
        d, n = "D_" + member, member
        if h.get("name") is not None:
            facts.append('<in-bse-shp:NameOfTheShareholder contextRef="%s">%s</in-bse-shp:NameOfTheShareholder>' % (d, h["name"]))
        if h.get("flag"):
            facts.append('<in-bse-shp:WhetherACategoryOrMoreThan1PercentageOfShareholding contextRef="%s">%s</in-bse-shp:WhetherACategoryOrMoreThan1PercentageOfShareholding>' % (d, h["flag"]))
        if h.get("shares") is not None:
            facts.append('<in-bse-shp:NumberOfFullyPaidUpEquityShares contextRef="%s" unitRef="u">%s</in-bse-shp:NumberOfFullyPaidUpEquityShares>' % (n, h["shares"]))
        if h.get("pct") is not None:
            facts.append('<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="%s" unitRef="u">%s</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>' % (n, h["pct"]))
    s = base.decode()
    return s.replace("</xbrl>", "".join(ctx) + "".join(facts) + "</xbrl>").encode()


def H(axis, n, name, shares, pct, **kw):
    return dict(axis=axis, cat=kw.pop("cat", axis.replace("DetailsOfSharesHeldBy", "").replace("Axis", "")), n=n, name=name, shares=shares, pct=pct, **kw)


def named(holders, rows=None, unit="fraction", report=REPORT):
    rows = rows if rows is not None else new_rows()
    data = with_holders(make_xbrl(rows, report), holders, report)
    parsed = su.parse_xbrl(data)
    agg = {n: r for (n, per), r in parsed["rows"].items() if per == report}
    return su.build_named_holders(data, report, unit, agg, "2026-10-05")


def names(nh):
    return [h["holder_name"] for h in nh["holders"]]


class Extract(unittest.TestCase):
    def test_classification_is_by_axis_not_by_name(self):
        nh = named([H("DetailsOfSharesHeldByMutualFundsOrUtiAxis", 15, "Alpha Insurance Corp", 60, 0.06),
                    H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "Beta Mutual Fund", 20, 0.02),
                    H("DetailsOfSharesHeldByOthersIndianShareholdersAxis", 15, "Gamma Public Ltd", 300, 0.3),
                    H("DetailsOfSharesHeldByForeignDirectInvestmentAxis", 15, "Delta Promoter Holdings", 100, 0.1),
                    H("DetailsOfSharesHeldByOverseasDepositoriesAxis", 15, "Epsilon", 10, 0.01),
                    H("DetailsOfSharesHeldByProvidentFundsOrPensionFundsAxis", 15, "Zeta", 5, 0.005)])
        lab = {h["holder_name"]: h["label"] for h in nh["holders"]}
        self.assertEqual(lab["Alpha Insurance Corp"], su.L_MF)
        self.assertEqual(lab["Beta Mutual Fund"], su.L_INS)
        self.assertEqual(lab["Gamma Public Ltd"], su.L_PROMOTER)
        self.assertEqual(lab["Delta Promoter Holdings"], su.L_FII)
        self.assertEqual(lab["Epsilon"], su.L_FII)
        self.assertEqual(lab["Zeta"], su.L_DII)

    def test_axes_verified_by_the_live_inventory(self):
        nh = named([H("DetailsSharesHeldByIndividualsOrHUFAxis", 1, "Person One", 300, 0.3),
                    H("DetailsOfSharesHeldByCentralGovernmentOrStateGovernmentsAxis", 1, "Government Holder", 200, 0.2),
                    H("DetailsOfSharesHeldByInstitutionsForeignPortfolioInvestorAxis", 1, "Old Fund", 100, 0.1),
                    H("DetailsOfSharesHeldByOtherNonInstitutionsAxis", 1, "Bodies Corporate", 90, 0.09),
                    H("DetailsOfSharesHeldByOtherInstitutionsAxis", 1, "Other", 80, 0.08)])
        lab = {h["holder_name"]: (h["label"], h["section"]) for h in nh["holders"]}
        self.assertEqual(lab["Person One"], (su.L_PROMOTER, "promoter_individuals"))
        self.assertEqual(lab["Government Holder"], (su.L_PROMOTER, "promoter_central_state_government"))
        self.assertEqual(lab["Old Fund"], (su.L_FII, "fpi_old_format"))
        self.assertEqual(sorted(lab), ["Government Holder", "Old Fund", "Person One"])      # category rows and placeholder-named rows are not holders
        self.assertEqual(sorted(nh["unmapped_axes"]), ["DetailsOfSharesHeldByOtherInstitutionsAxis", "DetailsOfSharesHeldByOtherNonInstitutionsAxis"])

    def test_percent_is_the_reported_value_converted_by_unit(self):
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 55200, 0.0552)])
        self.assertEqual(nh["holders"][0]["percentage"], 5.52)
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 55200, 5.52)], unit="percent")
        self.assertEqual(nh["holders"][0]["percentage"], 5.52)

    def test_missing_percentage_is_never_inferred(self):
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 55200, None)])
        self.assertIsNone(nh["holders"][0]["percentage"])
        self.assertEqual(nh["holders"][0]["shares"], 55200)

    def test_exclusions(self):
        nh = named([H("DetailsOfSharesHeldByOthersIndianShareholdersAxis", 15, "Real", 500, 0.5),
                    H("DetailsOfSharesHeldByOthersIndianShareholdersAxis", 16, "Zero A", 0, 0),
                    H("DetailsOfSharesHeldByOthersIndianShareholdersAxis", 17, "Zero B", None, None),
                    H("DetailsOfSharesHeldByMutualFundsOrUtiAxis", 15, "Mutual Funds", 60, 0.06, flag="Category"),
                    H("DetailsOfSharesHeldByMutualFundsOrUtiAxis", 16, "Held", 20, 0.02, flag="More than 1 percentage of shareholding"),
                    H("DetailsOfTheShareholdersActingAsPersonsInConcertForPublicAxis", 15, "Dup", 100, 0.1),
                    H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "***", 10, 0.01),
                    H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 16, None, 10, 0.01)])
        self.assertEqual(names(nh), ["Real", "Held"])
        ex = nh["excluded"]
        self.assertEqual((ex["zero_share"], ex["category_rows"], ex["persons_in_concert"], ex["unnamed"]), (2, 1, 1, 2))

    def test_unknown_axis_is_counted_not_guessed(self):
        nh = named([H("DetailsOfSharesHeldByBrandNewAxis", 15, "Mystery", 100, 0.1), H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 10, 0.01)])
        self.assertEqual(names(nh), ["A"])
        self.assertEqual(nh["unmapped_axes"], {"DetailsOfSharesHeldByBrandNewAxis": 1})

    def test_other_periods_are_ignored(self):
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "Old", 10, 0.01, period="2025-09-30"), H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 16, "Now", 10, 0.01)])
        self.assertEqual(names(nh), ["Now"])

    def test_ordering_percentage_desc_then_shares_then_name(self):
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "B", 10, 0.01), H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 16, "A", 10, 0.01),
                    H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 17, "Big", 5, 0.2), H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 18, "NoPct", 99, None)])
        self.assertEqual(names(nh), ["Big", "A", "B", "NoPct"])

    def test_no_double_counting_section_totals_reconcile_with_the_aggregate(self):
        rows = new_rows() + [("MutualFundsOrUti", 60, 6)]
        rows = [r for r in new_rows()]
        nh = named([H("DetailsOfSharesHeldByMutualFundsOrUtiAxis", 15, "F1", 40, 0.04), H("DetailsOfSharesHeldByMutualFundsOrUtiAxis", 16, "F2", 30, 0.03)], rows=rows)
        t = nh["section_totals"]["mutual_funds"]
        self.assertEqual((t["named_shares"], t["category_total_shares"], t["named_count"]), (70, 60, 2))
        self.assertTrue(any(f["code"] == "named-holders-exceed-category-total" for f in nh["flags"]))

    def test_status_when_nothing_is_listed(self):
        self.assertEqual(named([])["status"], "not-in-filing")
        nh = named([H("DetailsOfSharesHeldByOthersIndianShareholdersAxis", 15, "Z", 0, 0)])
        self.assertEqual(nh["status"], "none-disclosed")
        self.assertEqual(nh["holders"], [])

    def test_unparsable_is_unavailable(self):
        self.assertEqual(su.build_named_holders(b"<x", REPORT, "fraction", {}, "d")["status"], "unavailable")

    def test_conflicting_duplicate_facts_are_excluded(self):
        d = with_holders(make_xbrl(new_rows(), REPORT), [H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 10, 0.01)])
        d = d.replace(b"</xbrl>", b'<in-bse-shp:NumberOfFullyPaidUpEquityShares contextRef="InsuranceCompanies_Context15" unitRef="u">99</in-bse-shp:NumberOfFullyPaidUpEquityShares></xbrl>')
        nh = su.build_named_holders(d, REPORT, "fraction", {}, "d")
        self.assertEqual(nh["holders"], [])
        self.assertEqual(nh["excluded"]["conflicting"], 1)

    def test_disclosure_limitation_is_preserved(self):
        nh = named([H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "A", 10, 0.01)])
        self.assertIn("1% or more", nh["basis"])
        self.assertIn("not the sum", nh["basis"])

    def test_no_hardcoded_names_in_the_updater(self):
        src = open(su.__file__).read()
        for n in ("Tata", "LIC", "Life Insurance", "Deutsche", "Tobacco", "Nifty", "Prudential"):
            self.assertIsNone(re.search(r"\b%s\b" % n, src), n)

    def test_select_major_top_ten_and_available_only(self):
        hs = [H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15 + i, "H%02d" % i, 10 + i, round(0.01 + i / 1000, 4)) for i in range(14)]
        nh = named(hs)
        top = su.select_major(nh)
        self.assertEqual(len(top), 10)
        self.assertEqual([h["percentage"] for h in top], sorted((h["percentage"] for h in top), reverse=True))
        self.assertEqual(su.select_major(su.nh_state("unavailable", "x", "d")), [])


def fake(rows_fn, rid="1", date="31-DEC-2025", holders=()):
    url = "https://nsearchives.nseindia.com/corporate/x_%s.xml" % rid
    return index_rec(date, rid), {url: with_holders(make_xbrl(rows_fn(), REPORT), list(holders))}


class Pipeline(unittest.TestCase):
    HOLD = [H("DetailsOfSharesHeldByInsuranceCompaniesAxis", 15, "Insurer One", 55, 5.5)]

    def run_stock(self, nse, ledger=None, refresh=False):
        ledger = ledger if ledger is not None else sl.new_ledger()
        ev, _, err = su.process_stock(nse, ledger, "ITC", "2026-10-05", [100], refresh)
        self.assertIsNone(err)
        return ledger, ev

    def test_new_record_carries_named_holders_and_aggregates_are_unchanged(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, ev = self.run_stock(FakeNse({"ITC": [rec]}, files))
        q = led["stocks"]["ITC"]["quarters"][0]
        self.assertEqual(q["named_holders"]["status"], "available")
        files2 = {k: make_xbrl(new_rows(), REPORT) for k in files}
        led2, _ = self.run_stock(FakeNse({"ITC": [rec]}, files2))
        self.assertEqual(led2["stocks"]["ITC"]["quarters"][0]["values"], q["values"])      # the holders never alter the aggregates

    def test_gap_fill_adds_holders_without_touching_values(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        plain = {k: make_xbrl(new_rows(), REPORT) for k in files}
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, plain))
        q = led["stocks"]["ITC"]["quarters"][0]
        q.pop("named_holders")                                                                # an old-schema record
        before = copy.deepcopy(q)
        led2, ev = self.run_stock(FakeNse({"ITC": [rec]}, files), led)
        q2 = led2["stocks"]["ITC"]["quarters"][0]
        self.assertEqual(ev["2025-12-31"], "named-added")
        self.assertEqual(q2["named_holders"]["status"], "available")
        for k in before:
            self.assertEqual(q2[k], before[k], k)
        self.assertEqual(sl.no_loss_problems({"stocks": {"ITC": {"quarters": [before]}}}, led2), [])

    def test_gap_fill_refuses_when_aggregates_would_differ(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, {k: make_xbrl(new_rows(), REPORT) for k in files}))
        q = led["stocks"]["ITC"]["quarters"][0]
        q.pop("named_holders")
        vals = copy.deepcopy(q["values"])
        rows = [("NonInstitutions", 240, 24) if r[0] == "NonInstitutions" else r for r in new_rows()]
        other = {k: with_holders(make_xbrl(rows, REPORT), self.HOLD) for k in files}
        led2, ev = self.run_stock(FakeNse({"ITC": [rec]}, other), led)
        q2 = led2["stocks"]["ITC"]["quarters"][0]
        self.assertEqual(ev["2025-12-31"], "named-skipped")
        self.assertEqual(q2["values"], vals)
        self.assertEqual(q2["named_holders"]["status"], "unavailable")

    def test_failed_gap_fill_keeps_the_quarter_available(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, {k: make_xbrl(new_rows(), REPORT) for k in files}))
        led["stocks"]["ITC"]["quarters"][0].pop("named_holders")
        led2, ev = self.run_stock(FakeNse({"ITC": [rec]}, {}), led)
        q = led2["stocks"]["ITC"]["quarters"][0]
        self.assertEqual((q["status"], q["named_holders"]["status"], ev["2025-12-31"]), ("available", "unavailable", "named-unavailable"))

    def test_an_unavailable_marker_is_retried_and_a_read_result_is_not_redownloaded(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, files))
        nse = FakeNse({"ITC": [rec]}, files)
        _, ev = self.run_stock(nse, led)
        self.assertEqual(ev["2025-12-31"], "unchanged")
        self.assertEqual(nse.downloads, 0)

    def test_output_has_holders_and_major_list(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, files))
        doc = su.build_output(led, {}, "2026-10-05")
        st = doc["stocks"]["ITC"]
        self.assertEqual(st["major_holders"]["quarter_end"], "2025-12-31")
        self.assertEqual(st["major_holders"]["holders"][0], {"holder_name": "Insurer One", "category": "DII", "label": su.L_INS, "shares": 55, "percentage": 5.5})
        self.assertEqual(su.validate_doc(doc), [])
        self.assertEqual(doc["schema"], 1)

    def test_validate_catches_bad_named_holders(self):
        rec, files = fake(new_rows, holders=self.HOLD)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, files))
        doc = su.build_output(led, {}, "d")
        h = doc["stocks"]["ITC"]["quarters"][0]["named_holders"]["holders"]
        h[0]["shares"] = 0
        self.assertTrue(any("shares must be positive" in x for x in su.validate_doc(doc)))
        h[0]["shares"] = 5
        h[0]["label"] = "Mystery"
        self.assertTrue(su.validate_doc(doc))

    def test_unavailable_quarter_gets_unavailable_named_state(self):
        rec = index_rec("31-DEC-2025", "9", xbrl=None)
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, {}))
        q = led["stocks"]["ITC"]["quarters"][0]
        self.assertEqual((q["status"], q["named_holders"]["status"]), ("unavailable", "unavailable"))
        self.assertEqual(su.validate_doc(su.build_output(led, {}, "d")), [])

    def test_old_format_aggregate_with_holders(self):
        data = with_holders(make_xbrl(old_rows(), REPORT), self.HOLD)
        rec = index_rec("31-DEC-2025", "5")
        led, _ = self.run_stock(FakeNse({"ITC": [rec]}, {"https://nsearchives.nseindia.com/corporate/x_5.xml": data}))
        q = led["stocks"]["ITC"]["quarters"][0]
        self.assertEqual(q["format_version"], su.FORMAT_OLD)
        self.assertEqual(q["named_holders"]["status"], "available")


class NoLoss(unittest.TestCase):
    def doc(self, nh, rid="1"):
        return {"stocks": {"ITC": {"quarters": [{"quarter_end": REPORT, "status": "available", "source": {"record_id": rid, "xbrl_url": "https://x"}, "values": {}, "revisions": [], "named_holders": nh}]}}}

    def test_rules(self):
        good = su.nh_state("available", None, "d")
        good["holders"] = [{"holder_name": "A"}]
        self.assertEqual(sl.no_loss_problems(self.doc(good), self.doc(good)), [])
        self.assertTrue(sl.no_loss_problems(self.doc(good), self.doc(None)))                                   # lost
        self.assertTrue(sl.no_loss_problems(self.doc(good), self.doc(su.nh_state("unavailable", "x", "d"))))  # lost
        changed = copy.deepcopy(good)
        changed["holders"] = []
        self.assertTrue(sl.no_loss_problems(self.doc(good), self.doc(changed)))                               # changed, same record id
        self.assertTrue(sl.no_loss_problems(self.doc(good), self.doc(changed, rid="2")))                      # new id, no revision
        self.assertEqual(sl.no_loss_problems(self.doc(None), self.doc(good)), [])                             # gap-fill is allowed
        self.assertEqual(sl.no_loss_problems(self.doc(su.nh_state("unavailable", "x", "d")), self.doc(good)), [])

    def test_revision_with_snapshot_is_accepted(self):
        good = su.nh_state("available", None, "d")
        good["holders"] = [{"holder_name": "A"}]
        new = self.doc(su.nh_state("none-disclosed", "r", "d"), rid="2")
        rq = new["stocks"]["ITC"]["quarters"][0]
        rq["revisions"] = [{"source": {"record_id": "1"}, "values": {}, "named_holders": good}]
        self.assertEqual(sl.no_loss_problems(self.doc(good), new), [])


if __name__ == "__main__":
    unittest.main()
