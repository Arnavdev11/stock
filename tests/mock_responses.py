"""Mocked Upstox responses (shapes taken from the official docs). No network is used in tests."""


def income(**over):
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "income_statement": [
        {"category": "revenue", "history": [       # deliberately not in date order
            {"value": 982671, "period": "Mar 2025", "change": "+7.15%"},
            {"value": 1086181, "period": "Mar 2026", "change": "+10.53%"},
            {"value": 917121, "period": "Mar 2024"}]},
        {"category": "operating_profit", "history": [
            {"value": 123162, "period": "Mar 2026", "change": "+16.17%"}, {"value": 106017, "period": "Mar 2025"}]},
        {"category": "net_profit", "history": [
            {"value": -500.5, "period": "Mar 2026", "change": "-105.2%"}, {"value": 80787, "period": "Mar 2025"}]}]}
    d.update(over)
    return {"status": "success", "data": d}


def balance(**over):
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "history": [
        {"total_asset": 1755986, "total_liability": 830198, "period": "Mar 2024"},
        {"total_asset": 1950121, "total_liability": 940495, "period": "Mar 2025"}]}
    d.update(over)
    return {"status": "success", "data": d}


def cashflow(capex_lines=None, **over):
    hist = lambda a, b: [{"value": a, "period": "Mar 2026", "change": "+1%"}, {"value": b, "period": "Mar 2025"}]
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "cash_flow": [
        {"category": "operating", "history": hist(150000, 140000)},
        {"category": "investing", "history": hist(-90000, -80000)},
        {"category": "financing", "history": hist(-20000, -30000)}]}
    if capex_lines is not None:
        d["full_statement"] = [{"particular": n, "history": [{"period": p, "value": v}]} for n, p, v in capex_lines]
    d.update(over)
    return {"status": "success", "data": d}
