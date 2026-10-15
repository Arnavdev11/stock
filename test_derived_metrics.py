"""Offline tests for derived_metrics.py. Fixtures only: nothing here says anything about live provider coverage."""
import json
import unittest

import derived_metrics as dm

NOW = "2026-10-15T10:00:00Z"
PRICE = {"close": 200.0, "date": "2026-10-14"}


def ann(**kw):
    d = {"period": "Mar 2026", "basis": "consolidated", "eps_basic": 10.0, "eps_diluted": 8.0, "net_profit": 100.0, "unit": "INR crore"}
    d.update(kw)
    return d


def bal(**kw):
    d = {"period": "Mar 2026", "basis": "consolidated", "total_equity": 600.0, "total_assets": 1500.0, "current_liabilities": 300.0,
         "total_debt": None, "unit": "INR crore",
         "prior": {"period": "Mar 2025", "basis": "consolidated", "total_equity": 400.0, "total_assets": 1300.0, "current_liabilities": 250.0,
                   "unit": "INR crore"}}
    d.update(kw)
    return d


def q(end, rev=None, pat=None, total=None):
    return {"quarter_end": end, "values": {"revenue": rev, "total_revenue": total, "profit_after_tax": pat}}


def qdoc(rows, basis="consolidated"):
    return {"basis": basis, "quarters": rows}


class Formulas(unittest.TestCase):
    def test_pe_normal(self):
        v, r = dm.pe_from(200, 8, "2026-10-14", "2026-03-31")
        self.assertEqual((v, r), (25.0, None))

    def test_pe_missing_inputs(self):
        self.assertEqual(dm.pe_from(None, 8, "2026-10-14", "2026-03-31")[1], "price_missing_or_invalid")
        self.assertEqual(dm.pe_from(200, None, "2026-10-14", "2026-03-31")[1], "eps_missing")
        self.assertEqual(dm.pe_from(0, 8, "2026-10-14", "2026-03-31")[1], "price_missing_or_invalid")

    def test_pe_zero_and_negative_eps(self):
        self.assertEqual(dm.pe_from(200, 0, "2026-10-14", "2026-03-31")[1], "eps_zero")
        self.assertEqual(dm.pe_from(200, -3, "2026-10-14", "2026-03-31")[1], "eps_negative_pe_not_meaningful")

    def test_pe_incompatible_period_and_stale_price(self):
        self.assertEqual(dm.pe_from(200, 8, "2026-01-01", "2026-03-31")[1], "incompatible_periods_price_before_eps_period_end")
        import datetime as d
        self.assertEqual(dm.pe_from(200, 8, "2026-09-01", "2026-03-31", d.date(2026, 10, 15))[1], "price_stale")
        self.assertEqual(dm.pe_from(200, 8, None, "2026-03-31")[1], "price_or_eps_date_missing")

    def test_pb(self):
        self.assertEqual(dm.pb_from(200, 50), (4.0, None))
        self.assertEqual(dm.pb_from(200, None)[1], "book_value_per_share_missing")
        self.assertEqual(dm.pb_from(200, -5)[1], "book_value_non_positive_pb_not_meaningful")
        self.assertEqual(dm.pb_from(200, 0)[1], "book_value_non_positive_pb_not_meaningful")

    def test_roe_roa(self):
        v, r = dm.roe_from(100, 400, 600)
        self.assertAlmostEqual(v, 20.0)
        v, r = dm.roa_from(100, 1300, 1500)
        self.assertAlmostEqual(v, 100 / 1400 * 100)

    def test_roe_negative_earnings_is_kept_negative(self):
        v, r = dm.roe_from(-50, 400, 600)
        self.assertAlmostEqual(v, -10.0)

    def test_roe_zero_and_negative_equity(self):
        self.assertEqual(dm.roe_from(100, -100, 100)[1], "average_equity_zero")
        self.assertEqual(dm.roe_from(100, -500, 100)[1], "average_equity_negative_not_meaningful")
        self.assertEqual(dm.roe_from(100, None, 100)[1], "prior_year_equity_missing")
        self.assertEqual(dm.roe_from(None, 1, 1)[1], "net_profit_missing")

    def test_roce_ev_ebitda_debt_equity(self):
        self.assertAlmostEqual(dm.roce_from(120, 900, 1100)[0], 12.0)
        self.assertEqual(dm.roce_from(None, 900, 1100)[1], "ebit_missing")
        self.assertEqual(dm.ev_ebitda_from(1000, 100), (10.0, None))
        self.assertEqual(dm.ev_ebitda_from(1000, -5)[1], "ebitda_non_positive_not_meaningful")
        self.assertEqual(dm.ev_ebitda_from(1000, 0)[1], "ebitda_non_positive_not_meaningful")
        self.assertEqual(dm.ev_ebitda_from(1000, None)[1], "ebitda_missing")
        self.assertEqual(dm.debt_to_equity_from(300, 600), (0.5, None))
        self.assertEqual(dm.debt_to_equity_from(300, 0)[1], "equity_non_positive_not_meaningful")
        self.assertEqual(dm.debt_to_equity_from(300, -10)[1], "equity_non_positive_not_meaningful")
        self.assertEqual(dm.debt_to_equity_from(None, 10)[1], "total_borrowings_missing")
        self.assertEqual(dm.debt_to_equity_from(0, 10), (0.0, None))      # genuinely debt free is a real zero, missing is not

    def test_growth_rules(self):
        self.assertAlmostEqual(dm.growth_from(120, 100)[0], 20.0)
        self.assertAlmostEqual(dm.growth_from(80, 100)[0], -20.0)
        self.assertEqual(dm.growth_from(10, 0)[1], "previous_period_zero")
        self.assertEqual(dm.growth_from(10, -5)[1], "previous_period_negative")
        self.assertEqual(dm.growth_from(None, 5)[1], "current_value_missing")
        self.assertEqual(dm.growth_from(5, None)[1], "previous_value_missing")
        self.assertEqual(dm.growth_from(float("nan"), 5)[1], "current_value_missing")


class StockDerivation(unittest.TestCase):
    def derive(self, **kw):
        return dm.derive_stock(kw, NOW)

    def test_provider_value_is_never_replaced(self):
        m = self.derive(provider={"pe": 31.5}, price=PRICE, annual=ann())
        self.assertEqual(m["pe"], {"source": "provider", "value": 31.5})

    def test_provider_zero_is_a_value_provider_null_is_not(self):
        m = self.derive(provider={"pe": None}, price=PRICE, annual=ann())
        self.assertEqual(m["pe"]["source"], "calculated")
        self.assertEqual(m["pe"]["value"], 25.0)

    def test_calculated_record_carries_provenance(self):
        r = self.derive(provider={}, price=PRICE, annual=ann())["pe"]
        self.assertEqual(r["formula"], dm.FORMULAS["pe"])
        self.assertEqual(r["calculated_at"], NOW)
        self.assertIn("Mar 2026", r["input_period"])
        self.assertNotIn("reason", r)

    def test_pe_prefers_diluted_then_basic(self):
        self.assertEqual(self.derive(price=PRICE, annual=ann())["pe"]["value"], 25.0)
        self.assertEqual(self.derive(price=PRICE, annual=ann(eps_diluted=None))["pe"]["value"], 20.0)

    def test_missing_inputs_give_reason_not_zero(self):
        m = self.derive()
        for k in dm.METRICS:
            self.assertEqual(m[k]["source"], "unavailable", k)
            self.assertIsNone(m[k]["value"])
            self.assertTrue(m[k]["reason"])

    def test_roe_roa_need_prior_year_balance(self):
        m = self.derive(annual=ann(), balance=bal(prior=None))
        self.assertEqual(m["roe"]["reason"], "prior_year_balance_sheet_not_stored")
        self.assertEqual(m["roa"]["reason"], "prior_year_balance_sheet_not_stored")

    def test_roe_roa_calculated_when_inputs_exist(self):
        m = self.derive(annual=ann(), balance=bal())
        self.assertAlmostEqual(m["roe"]["value"], 20.0)
        self.assertEqual(m["roe"]["source"], "calculated")
        self.assertAlmostEqual(m["roa"]["value"], round(100 / 1400 * 100, 2))

    def test_negative_earnings_preserved(self):
        m = self.derive(annual=ann(net_profit=-60.0), balance=bal())
        self.assertAlmostEqual(m["roe"]["value"], -12.0)

    def test_negative_equity_not_meaningful(self):
        b = bal(total_equity=-900.0)
        b["prior"]["total_equity"] = -700.0
        m = self.derive(annual=ann(), balance=b)
        self.assertEqual(m["roe"]["reason"], "average_equity_negative_not_meaningful")

    def test_standalone_consolidated_mix_refused(self):
        b = bal()
        b["prior"]["basis"] = "standalone"
        m = self.derive(annual=ann(), balance=b)
        self.assertEqual(m["roe"]["reason"], "incompatible_basis_standalone_vs_consolidated")
        m = self.derive(annual=ann(basis="standalone"), balance=bal())
        self.assertEqual(m["roa"]["reason"], "incompatible_basis_standalone_vs_consolidated")

    def test_incompatible_periods_refused(self):
        b = bal()
        b["prior"]["period"] = "Mar 2024"                    # two years back, not one
        self.assertEqual(self.derive(annual=ann(), balance=b)["roe"]["reason"], "incompatible_periods")
        self.assertEqual(self.derive(annual=ann(period="Mar 2025"), balance=bal())["roe"]["reason"], "incompatible_periods")

    def test_unit_mismatch_refused(self):
        b = bal()
        b["prior"]["unit"] = "INR lakh"
        self.assertEqual(self.derive(annual=ann(), balance=b)["roe"]["reason"], "unit_mismatch")

    def test_banks_and_nbfcs_excluded_where_mechanical_ratios_mislead(self):
        m = self.derive(financial=True, annual=ann(), balance=bal(total_debt=900.0), ebit=100, ebitda=150, enterprise_value=5000)
        for k in ("roce", "ev_ebitda", "debt_to_equity"):
            self.assertEqual(m[k]["reason"], "not_applicable_financial_company", k)
        self.assertEqual(m["roe"]["source"], "calculated")                 # ROE and ROA remain meaningful for lenders
        self.assertEqual(m["roa"]["source"], "calculated")

    def test_non_financial_ratios_calculate_when_inputs_exist(self):
        m = self.derive(financial=False, annual=ann(), balance=bal(total_debt=300.0), ebit=120.0, ebitda=150.0, enterprise_value=1500.0, bvps=50.0, price=PRICE)
        self.assertEqual(m["debt_to_equity"]["value"], 0.5)
        self.assertEqual(m["ev_ebitda"]["value"], 10.0)
        self.assertEqual(m["pb"]["value"], 4.0)
        self.assertEqual(m["roce"]["source"], "calculated")

    def test_unknown_sector_blocks_mechanical_ratios_but_keeps_input_reasons(self):
        full = self.derive(annual=ann(), balance=bal(total_debt=300.0), ebit=120.0, ebitda=150.0, enterprise_value=1500.0)   # financial not stated
        for k in ("roce", "ev_ebitda", "debt_to_equity"):
            self.assertEqual(full[k]["reason"], "sector_unknown_cannot_verify_non_financial", k)
        thin = self.derive(annual=ann(), balance=bal())
        self.assertEqual(thin["debt_to_equity"]["reason"], "total_borrowings_missing")           # the real gap is still what is reported

    def test_sector_name_never_written(self):
        out = json.dumps(self.derive(financial=True, sector="Banks", annual=ann(), balance=bal()))
        self.assertNotIn("Banks", out)
        self.assertNotIn("sector", out)


class GrowthDerivation(unittest.TestCase):
    def test_same_quarter_year_on_year(self):
        d = qdoc([q("2026-06-30", rev=120, pat=30), q("2026-03-31", rev=110, pat=25), q("2025-06-30", rev=100, pat=20)])
        m = dm.derive_stock({"quarterly": d}, NOW)
        self.assertEqual(m["revenue_growth"]["value"], 20.0)
        self.assertEqual(m["profit_growth"]["value"], 50.0)
        self.assertEqual(m["revenue_growth"]["source"], "calculated")
        self.assertIn("2026-06-30 vs 2025-06-30", m["revenue_growth"]["input_period"])

    def test_sequential_quarter_is_not_used_as_yoy(self):
        d = qdoc([q("2026-06-30", rev=120, pat=30), q("2026-03-31", rev=100, pat=20)])
        m = dm.derive_stock({"quarterly": d}, NOW)
        self.assertEqual(m["revenue_growth"]["reason"], "same_quarter_prior_year_missing")

    def test_zero_and_negative_prior(self):
        d = qdoc([q("2026-06-30", rev=120, pat=30), q("2025-06-30", rev=0, pat=-5)])
        m = dm.derive_stock({"quarterly": d}, NOW)
        self.assertEqual(m["revenue_growth"]["reason"], "previous_period_zero")
        self.assertEqual(m["profit_growth"]["reason"], "previous_period_negative")

    def test_missing_prior_value_is_not_zero(self):
        d = qdoc([q("2026-06-30", rev=120, pat=30), q("2025-06-30", rev=None, pat=None)])
        m = dm.derive_stock({"quarterly": d}, NOW)
        self.assertEqual(m["revenue_growth"]["source"], "unavailable")

    def test_revenue_fields_never_mixed(self):
        d = qdoc([q("2026-06-30", rev=None, total=130), q("2025-06-30", rev=100, total=None)])
        m = dm.derive_stock({"quarterly": d}, NOW)
        self.assertEqual(m["revenue_growth"]["source"], "unavailable")

    def test_total_revenue_used_when_revenue_absent_in_both(self):
        d = qdoc([q("2026-06-30", total=130), q("2025-06-30", total=100)])
        self.assertEqual(dm.derive_stock({"quarterly": d}, NOW)["revenue_growth"]["value"], 30.0)

    def test_provider_annual_growth_preferred_when_verified(self):
        d = qdoc([q("2026-06-30", rev=120, pat=30), q("2025-06-30", rev=100, pat=20)])
        m = dm.derive_stock({"quarterly": d, "annual": ann(total_revenue_growth=7.5, net_profit_growth=9.1, growth_verified=True)}, NOW)
        self.assertEqual(m["revenue_growth"], dm.provider(7.5, "FY Mar 2026 (consolidated)"))
        self.assertEqual(m["profit_growth"]["source"], "provider")

    def test_unverified_provider_growth_is_not_used(self):
        d = qdoc([q("2026-06-30", rev=120), q("2025-06-30", rev=100)])
        m = dm.derive_stock({"quarterly": d, "annual": ann(total_revenue_growth=7.5, growth_verified=False)}, NOW)
        self.assertEqual(m["revenue_growth"]["source"], "calculated")

    def test_unknown_basis_refused(self):
        d = qdoc([q("2026-06-30", rev=120), q("2025-06-30", rev=100)], basis=None)
        self.assertEqual(dm.derive_stock({"quarterly": d}, NOW)["revenue_growth"]["reason"], "reporting_basis_unknown")


class RealShapes(unittest.TestCase):
    fin = {"symbol": "ABC", "income": {"period": "Mar 2026", "basis": "consolidated", "eps_basic": 10.0, "eps_diluted": 8.0, "net_profit": 100.0,
                                       "total_revenue_growth": 12.0, "net_profit_growth": 15.0,
                                       "checks": {"summary_revenue_equals_total_revenue": True, "summary_net_profit_equals_pat": False}},
           "balance_sheet": {"period": "Mar 2026", "basis": "consolidated", "total_equity": 600.0, "total_assets": 1500.0, "total_debt": None}}

    def test_mapping_and_verification_gate(self):
        i = dm.inputs_from({"symbol": "ABC", "pe": None, "roe": 18.0}, self.fin, None, PRICE)
        m = dm.derive_stock(i, NOW)
        self.assertEqual(m["roe"], {"source": "provider", "value": 18.0})
        self.assertEqual(m["pe"]["value"], 25.0)
        self.assertEqual(m["revenue_growth"]["source"], "provider")           # verified check
        self.assertEqual(m["profit_growth"]["reason"], "no_quarterly_data")  # unverified provider growth ignored, no quarters
        self.assertEqual(m["roa"]["reason"], "prior_year_balance_sheet_not_stored")
        self.assertEqual(m["debt_to_equity"]["reason"], "total_borrowings_missing")

    def test_garbage_rows_do_not_raise(self):
        for args in ((None, None, None, None), ("x", 3, [], 4), ({}, {"income": 5, "balance_sheet": "b"}, {}, {})):
            self.assertEqual(set(dm.derive_stock(dm.inputs_from(*args), NOW)), set(dm.METRICS))


class Robustness(unittest.TestCase):
    def test_invalid_source_data_does_not_raise(self):
        junk = [None, 5, "x", [], {"annual": "bad", "balance": 7, "price": [], "quarterly": "q"},
                {"annual": {"period": 5, "eps_basic": "NaN"}, "price": {"close": "x"}},
                {"quarterly": {"basis": "consolidated", "quarters": [None, 3, {"quarter_end": "nope"}]}},
                {"annual": ann(), "balance": {"period": "Mar 2026", "basis": "consolidated", "prior": "oops"}}]
        for j in junk:
            m = dm.derive_stock(j, NOW)
            self.assertEqual(set(m), set(dm.METRICS))

    def test_nan_and_inf_inputs_rejected(self):
        m = dm.derive_stock({"price": {"close": float("inf"), "date": "2026-10-14"}, "annual": ann()}, NOW)
        self.assertEqual(m["pe"]["source"], "unavailable")

    def test_one_bad_stock_does_not_stop_the_universe(self):
        rows = dm.build([{"symbol": "AAA", "price": PRICE, "annual": ann()}, {"symbol": "BAD", "annual": object()},
                         {"symbol": ""}, "junk", {"symbol": "CCC"}], NOW)
        self.assertEqual([r["symbol"] for r in rows], ["AAA", "BAD", "CCC"])
        self.assertEqual(rows[0]["metrics"]["pe"]["source"], "calculated")
        self.assertEqual(rows[1]["metrics"]["pe"]["source"], "unavailable")


class CoverageMeasurement(unittest.TestCase):
    def test_before_after_and_reasons(self):
        stocks = [{"symbol": "A", "provider": {"pe": 20.0}, "price": PRICE, "annual": ann()},
                  {"symbol": "B", "provider": {"pe": None}, "price": PRICE, "annual": ann()},
                  {"symbol": "C", "provider": {"pe": None}, "annual": ann()},
                  {"symbol": "D", "provider": {"pe": None}, "price": PRICE, "annual": ann(eps_basic=-1.0, eps_diluted=-1.0)}]
        c = dm.coverage(dm.build(stocks, NOW))["metrics"]["pe"]
        self.assertEqual((c["provider"], c["calculated"], c["unavailable"]), (1, 1, 2))
        self.assertEqual(c["before_pct"], 25.0)
        self.assertEqual(c["after_pct"], 50.0)
        self.assertEqual(c["reasons"], {"price_missing_or_invalid": 1, "eps_negative_pe_not_meaningful": 1})

    def test_document_is_strict_json_and_states_feasibility(self):
        doc = dm.document(dm.build([{"symbol": "A"}], NOW), NOW)
        text = json.dumps(doc, allow_nan=False)
        self.assertIn("inputs absent", text)
        self.assertEqual(doc["coverage"]["metrics"]["pe"]["after_pct"], 0.0)

    def test_empty_universe(self):
        self.assertEqual(dm.coverage([])["metrics"]["pe"]["after_pct"], None)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- the read-only adapter / CLI (fixture files only)
import contextlib
import hashlib
import io
import tempfile
from pathlib import Path


def put(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def make_out(root):
    o = root / "out"
    put(o / "stock_directory.json", {"symbols": [{"symbol": s, "isin": "INE%08d1" % i, "name": s} for i, s in enumerate(["AAA", "BANKX", "CCC", "DDD"])]})
    put(o / "fundamentals.json", {"as_of": "2026-10-14", "source": "Upstox", "stocks": [
        {"symbol": "AAA", "pe": None, "pb": 3.0, "roa": None, "roe": 18.0, "roce": None, "ev_ebitda": None, "revenue_growth": None, "profit_growth": None, "sector": None},
        {"symbol": "BANKX", "pe": 12.0, "pb": 1.5, "roa": 1.1, "roe": 14.0, "roce": None, "ev_ebitda": None, "revenue_growth": None, "profit_growth": None, "sector": "Banks"},
        {"symbol": "CCC", "pe": None, "pb": None, "roa": None, "roe": None, "roce": None, "ev_ebitda": None, "revenue_growth": None, "profit_growth": None, "sector": None}]})
    fin = lambda sym, eps: {"symbol": sym, "income": {"period": "Mar 2026", "basis": "consolidated", "eps_basic": eps, "eps_diluted": None, "net_profit": 50.0,
                                                       "total_revenue_growth": None, "net_profit_growth": None, "checks": {}},
                            "balance_sheet": {"period": "Mar 2026", "basis": "consolidated", "total_equity": 300.0, "total_assets": 900.0, "total_debt": None}}
    put(o / "financials.json", {"schema": 2, "stocks": [fin("AAA", 10.0), fin("CCC", -2.0)]})
    put(o / "market_dma.json", {"as_of": "2026-10-14", "stocks": [{"symbol": "AAA", "close": 200.0}, {"symbol": "CCC", "close": 50.0}]})
    qs = lambda r1, r0: {"stocks": {"AAA": {"basis": "consolidated", "quarters": [
        {"quarter_end": "2026-06-30", "values": {"revenue": r1, "profit_after_tax": 30}}, {"quarter_end": "2025-06-30", "values": {"revenue": r0, "profit_after_tax": 20}}]}}}
    put(o / "by_symbol" / "quarterly_results" / "AAA.json", qs(120, 100))
    (o / "by_symbol" / "quarterly_results" / "DDD.json").write_text("{not json", encoding="utf-8")


def tree_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


class Adapter(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        make_out(self.d)

    def cli(self, *args):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = dm.main(["--data-dir", str(self.d), "--now", NOW] + list(args))
        return rc, buf.getvalue(), err.getvalue()

    def test_writes_separate_output_and_never_touches_inputs(self):
        before = tree_hash(self.d)
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0)
        after = tree_hash(self.d)
        new = set(after) - set(before)
        self.assertEqual(new, {"out/derived_metrics.json"})
        self.assertTrue(all(after[k] == before[k] for k in before))          # every input byte-identical
        doc = json.loads((self.d / "out" / "derived_metrics.json").read_text())
        self.assertEqual(doc["coverage"]["stocks"], 4)                       # the directory is the universe
        self.assertEqual(doc["inputs"]["universe_source"], "stock_directory.json")

    def test_values_and_provenance(self):
        self.cli()
        doc = json.loads((self.d / "out" / "derived_metrics.json").read_text())
        m = {r["symbol"]: r["metrics"] for r in doc["stocks"]}
        self.assertEqual(m["AAA"]["pe"]["value"], 20.0)                      # 200 / basic EPS 10 (diluted absent)
        self.assertEqual(m["AAA"]["pe"]["source"], "calculated")
        self.assertEqual(m["AAA"]["roe"], {"source": "provider", "value": 18.0})
        self.assertEqual(m["AAA"]["revenue_growth"]["value"], 20.0)
        self.assertEqual(m["AAA"]["profit_growth"]["value"], 50.0)
        self.assertEqual(m["AAA"]["roa"]["reason"], "prior_year_balance_sheet_not_stored")
        self.assertEqual(m["CCC"]["pe"]["reason"], "eps_negative_pe_not_meaningful")
        self.assertEqual(m["BANKX"]["pe"], {"source": "provider", "value": 12.0})
        self.assertEqual(m["BANKX"]["roce"]["reason"], "not_applicable_financial_company")   # sector read internally from the row
        self.assertEqual(m["DDD"]["pe"]["source"], "unavailable")             # damaged shard + no rows: still gets an entry
        self.assertNotIn("Banks", json.dumps(doc))

    def test_coverage_counts_reconcile(self):
        self.cli()
        c = json.loads((self.d / "out" / "derived_metrics.json").read_text())["coverage"]["metrics"]
        for name, x in c.items():
            self.assertEqual(x["provider"] + x["calculated"] + x["unavailable"], 4, name)
        self.assertEqual((c["pe"]["provider"], c["pe"]["calculated"]), (1, 1))
        self.assertEqual(c["pe"]["before_pct"], 25.0)
        self.assertEqual(c["pe"]["after_pct"], 50.0)
        self.assertEqual(c["roce"]["calculated"], 0)

    def test_financial_symbols_file(self):
        f = self.d / "fin.txt"
        f.write_text("AAA\n", encoding="utf-8")
        self.cli("--financial-symbols", str(f))
        m = {r["symbol"]: r["metrics"] for r in json.loads((self.d / "out" / "derived_metrics.json").read_text())["stocks"]}
        self.assertEqual(m["AAA"]["debt_to_equity"]["reason"], "not_applicable_financial_company")

    def test_no_real_files_writes_nothing(self):
        e = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(e, ignore_errors=True))
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = dm.main(["--data-dir", str(e)])
        self.assertEqual(rc, 1)
        self.assertIn("no usable fundamentals.json", err.getvalue())
        self.assertEqual(list(e.rglob("*")), [])

    def test_refuses_to_overwrite_an_input(self):
        rc, _, err = self.cli("--out", str(self.d / "out" / "fundamentals.json"))
        self.assertEqual(rc, 2)
        self.assertIn("refusing", err)

    def test_no_network_is_attempted(self):
        import socket
        from unittest import mock
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            rc, _, _ = self.cli()
        self.assertEqual(rc, 0)

    def test_report_only_writes_nothing(self):
        before = tree_hash(self.d)
        rc, out, _ = self.cli("--report-only")
        self.assertEqual(rc, 0)
        self.assertEqual(tree_hash(self.d), before)
        self.assertIn("provider", out)

    def test_nan_in_an_input_is_treated_as_unreadable(self):
        (self.d / "out" / "market_dma.json").write_text('{"as_of":"2026-10-14","stocks":[{"symbol":"AAA","close":NaN}]}', encoding="utf-8")
        rc, _, _ = self.cli()
        self.assertEqual(rc, 0)
        m = {r["symbol"]: r["metrics"] for r in json.loads((self.d / "out" / "derived_metrics.json").read_text())["stocks"]}
        self.assertEqual(m["AAA"]["pe"]["reason"], "price_missing_or_invalid")

    def test_unreadable_inputs_fail_clearly_and_write_nothing(self):
        for n in ("fundamentals.json", "financials.json"):
            (self.d / "out" / n).write_text("{broken", encoding="utf-8")
        before = tree_hash(self.d)
        rc, _, err = self.cli()
        self.assertEqual(rc, 1)
        self.assertIn("unreadable", err)
        self.assertEqual(tree_hash(self.d), before)

    def test_known_non_financial_sector_label_allows_ratios(self):
        self.assertIs(dm.classify_financial("Metals"), False)
        self.assertIs(dm.classify_financial("Private Sector Bank"), True)
        self.assertIs(dm.classify_financial(None, listed=True), True)
        self.assertIsNone(dm.classify_financial(None))
        self.assertIsNone(dm.classify_financial("  "))

    def test_unsafe_symbol_never_builds_a_path(self):
        put(self.d / "out" / "stock_directory.json", {"symbols": [{"symbol": "../evil"}, {"symbol": "AAA"}]})
        rc, _, _ = self.cli()
        self.assertEqual(rc, 0)
