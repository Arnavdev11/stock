"""Mocked Upstox responses. Numbers and shapes follow the official docs sample (fs=true). No network is used."""


def _h(*pairs, change=None):
    out = [{"value": v, "period": p} for p, v in pairs]
    for e, c in zip(out, change or []):
        if c:
            e["change"] = c
    return out


def _fs(particular, **by_period):
    return {"particular": particular, "history": [{"period": p.replace("_", " "), "value": v} for p, v in by_period.items()]}


def income(**over):
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "income_statement": [
        {"category": "revenue", "history": _h(("Mar 2026", 1086181), ("Mar 2025", 982671), ("Mar 2024", 917121), ("Mar 2023", 889569),
                                              change=["+10.53%", "+7.15%", "+3.1%"])},
        {"category": "operating_profit", "history": _h(("Mar 2026", 123162), ("Mar 2025", 106017), ("Mar 2024", 104340), ("Mar 2023", 94046),
                                                       change=["+16.17%", "+1.61%", "+10.95%"])},
        {"category": "net_profit", "history": _h(("Mar 2026", 95610), ("Mar 2025", 80787), ("Mar 2024", 78633), ("Mar 2023", 74088),
                                                 change=["+18.35%", "+2.74%", "+6.13%"])}],
        "full_statement": [
            _fs("Revenue", Mar_2025=964693, Mar_2024=901064), _fs("Other Income", Mar_2025=17978, Mar_2024=16057),
            _fs("Total Revenue", Mar_2025=982671, Mar_2024=917121), _fs("Total Expenses", Mar_2025=876654, Mar_2024=812781),
            _fs("Profit Before Tax", Mar_2025=106017, Mar_2024=104340), _fs("Tax", Mar_2025=25230, Mar_2024=25707),
            _fs("Profit After Tax", Mar_2025=80787, Mar_2024=78633), _fs("EPS - Basic", Mar_2025=51.47, Mar_2024=51.45),
            _fs("EPS - Diluted", Mar_2025=51.47, Mar_2024=51.45)]}
    d.update(over)
    return {"status": "success", "data": d}


def balance(extra_lines=(), **over):
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "history": [
        {"total_asset": 1950121, "total_liability": 940495, "period": "Mar 2025"},
        {"total_asset": 1755986, "total_liability": 830198, "period": "Mar 2024"}],
        "full_statement": [
            _fs("Non-Current Assets", Mar_2025=1450851, Mar_2024=1285886), _fs("Current Assets", Mar_2025=499270, Mar_2024=470100),
            _fs("Total Assets", Mar_2025=1950121, Mar_2024=1755986), _fs("Current Liabilities", Mar_2025=453737, Mar_2024=397367),
            _fs("Net Current Asset", Mar_2025=45533, Mar_2024=72733), _fs("Non-Current Liabilities", Mar_2025=486758, Mar_2024=432831),
            _fs("Equity Capital", Mar_2025=1009626, Mar_2024=925788),
            _fs("Total Equity & Liabilities", Mar_2025=1950121, Mar_2024=1755986)] + list(extra_lines)}
    d.update(over)
    return {"status": "success", "data": d}


def cashflow(lines=None, **over):
    hist = lambda a, b: [{"value": a, "period": "Mar 2026", "change": "+1%"}, {"value": b, "period": "Mar 2025"}]
    d = {"type": "consolidated", "time_period": "yearly", "units_in": "crore", "cash_flow": [
        {"category": "operating", "history": hist(150000, 140000)},
        {"category": "investing", "history": hist(-90000, -80000)},
        {"category": "financing", "history": hist(-20000, -30000)}],
        "full_statement": [_fs("Net Cash from Operating Activities", Mar_2026=150000),
                           _fs("Net Cash from Investing Activities", Mar_2026=-90000),
                           _fs("Net Cash from Financing Activities", Mar_2026=-20000)]}
    for label, by in (lines or []):
        d["full_statement"].append({"particular": label, "history": [{"period": p, "value": v} for p, v in by.items()]})
    d.update(over)
    return {"status": "success", "data": d}
