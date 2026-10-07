"""
Mutation test for the Phase 5K market data logic.   Run from the repository root:   python3 tests/mutation_market_5k.py

Each mutant is ONE deliberate change to the logic (a flipped comparison, a wrong window, a skipped check). It is applied to a COPY of the code in a temporary
folder (the repository is never touched) and the Python tests are run against it. A mutant that makes no test fail "survived" = the tests do not really
check that rule. Exit code 1 if any mutant survived or a pattern was not found.
"""
import concurrent.futures as cf
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["market_derive.py", "market_data_updater.py", "validate_market_outputs.py", "index_data_updater.py", "historical_updater.py", "upstox_common.py", "test_market_data.py", "test_index_data.py"]
D, U, V, X = "market_derive.py", "market_data_updater.py", "validate_market_outputs.py", "index_data_updater.py"
M = [
    # ---- calculations
    (D, "one-day change sign", "ch = round(c - p, 4)", "ch = round(p - c, 4)"),
    (D, "change: missing previous close accepted", "if p is None or c is None or p <= 0 or c <= 0:\n        return None, None", "if c is None or c <= 0:\n        return None, None"),
    (D, "dma: exactly N observations rejected", "if len(series) < period:\n        return out", "if len(series) <= period:\n        return out"),
    (D, "dma: fewer than N observations accepted", "if len(series) < period:\n        return out", "if len(series) < period - 1:\n        return out"),
    (D, "dma divisor", "sum(r[\"close\"] for r in win) / period", "sum(r[\"close\"] for r in win) / (period + 1)"),
    (D, "dma: equal counts as above", "\"above\" if last[\"close\"] > v", "\"above\" if last[\"close\"] >= v"),
    (D, "dma: span check removed", "> period * cfg[\"dma_span_factor\"]:", "> 10 ** 9:"),
    (D, "event on first day of window counted", "any(e[\"date\"] > first_date for e in events)", "any(e[\"date\"] >= first_date for e in events)"),
    (D, "event ignored", "any(e[\"date\"] > first_date for e in events)", "False"),
    (D, "breadth: missing history counted in denominator", "if d is None or d[\"status\"] == INSUFFICIENT:\n            ins += 1", "if d is None:\n            ins += 1\n        elif d[\"status\"] == INSUFFICIENT:\n            den += 1"),
    (D, "breadth: adjustment-flagged counted as safe", "elif d[\"status\"] == ADJUST:\n            adj += 1", "elif d[\"status\"] == \"never\":\n            adj += 1"),
    (D, "breadth percentage", "pct = _r(num / den * 100, 2) if den else None", "pct = _r(num / (den + 1) * 100, 2) if den else None"),
    (D, "52w: 252 prior days not required", "if len(series) < W + 1:", "if len(series) < W:"),
    (D, "52w: latest day inside its own window", "win = series[-(W + 1):-1]", "win = series[-W:]"),
    (D, "52w: equal to prior high counts as new", "\"new_high\" if cur[\"close\"] > ext else", "\"new_high\" if cur[\"close\"] >= ext else"),
    (D, "52w: equal to prior low counts as new", "\"new_low\" if cur[\"close\"] < ext else", "\"new_low\" if cur[\"close\"] <= ext else"),
    (D, "52w: high uses min", "ext = max(vals) if kind == \"high\" else min(vals)", "ext = min(vals) if kind == \"high\" else max(vals)"),
    (D, "52w: near threshold direction", "cur[\"close\"] >= ext * (1 - cfg[\"near_pct\"] / 100)", "cur[\"close\"] >= ext * (1 + cfg[\"near_pct\"] / 100)"),
    (D, "52w: prior extreme date", "when = [r[\"date\"] for r in window if r[field] == ext][-1]", "when = window[0][\"date\"]"),
    (D, "52w: distance sign", "dist = _r((cur[\"close\"] / ext - 1) * 100, 2)", "dist = _r((ext / cur[\"close\"] - 1) * 100, 2)"),
    (D, "52w: span check removed", "> cfg[\"hl_max_span_days\"]:", "> 10 ** 9:"),
    (D, "volume: today inside its own baseline", "win = series[-(P + 1):-1]\n    if any(r.get(\"volume\") is None", "win = series[-P:]\n    if any(r.get(\"volume\") is None"),
    (D, "volume: baseline length not enforced", "len(series) < P + 1:", "len(series) < 3:"),
    (D, "volume multiple", "out[\"multiple\"], out[\"status\"] = _r(cur[\"volume\"] / avg, 2), SAFE", "out[\"multiple\"], out[\"status\"] = _r(avg / cur[\"volume\"], 2) if cur[\"volume\"] else 0.0, SAFE"),
    (D, "volume: zero baseline accepted", "if avg <= 0:", "if avg < 0:"),
    (D, "breadth: unchanged counted as advancing", "if ch > 0:\n            adv += 1", "if ch >= 0:\n            adv += 1"),
    (D, "A/D ratio: zero decliners not guarded", "    if dec == 0:\n        out[\"ad_ratio_note\"]", "    if dec == -1:\n        out[\"ad_ratio_note\"]"),
    (D, "most active: value and volume swapped", "return {\"by_value\": top(\"turnover\"), \"by_volume\": top(\"volume\")}", "return {\"by_value\": top(\"volume\"), \"by_volume\": top(\"turnover\")}"),
    (D, "most active: zero included", "if r.get(key) is not None and r[key] > 0]", "if r.get(key) is not None and r[key] >= 0]"),
    (D, "most active: ascending", "c.sort(key=lambda r: (-r[key], r[\"symbol\"]))", "c.sort(key=lambda r: (r[key], r[\"symbol\"]))"),
    (D, "rank starts at zero", "dict(r, rank=i + 1)", "dict(r, rank=i)"),
    (D, "liquidity cut-off exclusive", "return t is not None and t >= cfg[\"liquid_min_turnover_lakhs\"]", "return t is not None and t > cfg[\"liquid_min_turnover_lakhs\"]"),
    (D, "movers: gainers ascending", "key=lambda x: (-x[0], x[1][\"symbol\"]))\n    lose", "key=lambda x: (x[0], x[1][\"symbol\"]))\n    lose"),
    (D, "movers: no liquidity filter", "        if not is_liquid(r, cfg):\n            continue\n        ch, pct = change_of(r)", "        ch, pct = change_of(r)"),
    (D, "shocker threshold exclusive", "if x[0] >= cfg[\"vol_multiple_min\"]]", "if x[0] > cfg[\"vol_multiple_min\"]]"),
    (D, "high-volume gain exclusive", "x[1] >= cfg[\"hv_gain_pct\"]]", "x[1] > cfg[\"hv_gain_pct\"]]"),
    (D, "high-volume loss exclusive", "x[1] <= cfg[\"hv_loss_pct\"]]", "x[1] < cfg[\"hv_loss_pct\"]]"),
    (D, "shockers: illiquid allowed", "or not is_liquid(r, cfg) or not r.get(\"volume\"):", "or not r.get(\"volume\"):"),
    (D, "corporate action only upward", "if abs(ratio - 1) > cfg[\"ca_threshold\"]:", "if ratio - 1 > cfg[\"ca_threshold\"]:"),
    (D, "corporate action threshold huge", "if abs(ratio - 1) > cfg[\"ca_threshold\"]:", "if abs(ratio - 1) > 5:"),
    # ---- loader, gate, writer
    (U, "universe change sign", "if abs(ch) > cfg[\"max_universe_change\"]:", "if ch > cfg[\"max_universe_change\"]:"),
    (U, "stale data allowed", "if stale > cfg[\"max_stale_days\"]:", "if stale > 10 ** 6:"),
    (U, "series filter removed", "if r.get(\"SERIES\", \"\").strip() != \"EQ\":", "if False:"),
    (U, "conflicting duplicates kept", "                c[\"conflicting_duplicate_symbols\"] += 1\n                continue", "                c[\"conflicting_duplicate_symbols\"] += 1"),
    (U, "delivery range", "if dp is not None and not 0 <= dp <= 100:", "if dp is not None and not 0 <= dp <= 1000:"),
    (U, "negative volume kept", "if vol is not None and vol < 0:", "if vol is not None and vol < -1e18:"),
    (U, "negative turnover kept", "if tov is not None and tov < 0:", "if tov is not None and tov < -1e18:"),
    (U, "close of zero accepted", "if close is None or close <= 0:", "if close is None or close < 0:"),
    (U, "expected day ignores the evening cut-off", "(now.hour, now.minute) >= (20, 30)", "(now.hour, now.minute) >= (0, 0)"),
    (U, "weekend files accepted", "name_date.weekday() >= 5:", "name_date.weekday() >= 7:"),
    (U, "future files accepted", "if raw is not None and name_date > today_ist:", "if False:"),
    (U, "partial files accepted", "if c[\"eq_rows\"] < cfg[\"min_eq_rows_per_file\"]:", "if c[\"eq_rows\"] < 0:"),
    (U, "newer rejected file ignored", "if errors_placeholder", "if errors_placeholder") if False else (U, "newer rejected file ignored", "if newer_rejected:", "if False:"),
    (U, "universe minimum ignored", "if n < cfg[\"min_universe\"]:", "if n < 0:"),
    (U, "no-loss: older data allowed", "if new_breadth[\"as_of\"] < old[\"as_of\"]:", "if False:"),
    (U, "no-loss: smaller universe allowed", "new_breadth[\"universe_count\"] < oc * (1 - cfg[\"max_no_loss_drop\"])", "False"),
    (U, "30D/90D published as safe", "metric[\"performance_30d_90d_all_market\"] = md.ADJUST", "metric[\"performance_30d_90d_all_market\"] = md.SAFE"),
    (U, "freshness mislabelled", "\"freshness\": \"EOD\",", "\"freshness\": \"LIVE\","),
    (U, "liquid breadth uses everything", "\"liquid\": md.breadth(liquid)}", "\"liquid\": md.breadth(today_rows)}"),
    (U, "latest-day flag always true", "is_latest = dq.get(\"source_is_latest_trading_day\", False)", "is_latest = True"),
    (U, "output validation skipped", "problems = vmo.validate(docs)", "problems = []"),
    (U, "date mismatch tolerated", "if wrong:", "if wrong > 10 ** 9:"),
    (U, "malformed share tolerated", "if bad / len(rows) > 0.01:", "if bad / len(rows) > 2:"),
    # ---- schema validator
    (V, "validator: percentage not recomputed", "abs(d[\"pct\"] - round(n / m * 100, 2)) > 0.011", "False"),
    (V, "validator: rank check", "if r.get(\"rank\") != i + 1:", "if False:"),
    (V, "validator: sort direction", "any((a < b) if desc else (a > b) for a, b in zip(vals, vals[1:]))", "any((a > b) if desc else (a < b) for a, b in zip(vals, vals[1:]))"),
    (V, "validator: freshness must be EOD", "if doc.get(\"freshness\") != \"EOD\":", "if False:"),
    (V, "validator: NaN allowed", "if isinstance(x, float) and not math.isfinite(x):", "if False:"),
    (V, "validator: weekend as_of allowed", "elif d.weekday() >= 5:", "elif False:"),
    (V, "validator: quality gate must pass", "if dq.get(\"status\") != \"pass\":", "if False:"),
    # ---- index module
    (X, "index: ambiguous accepted", "elif len(hits) > 1:", "elif len(hits) > 99:"),
    (X, "index: substring accepted", "if norm(r.get(\"name\")) in want or norm(r.get(\"trading_symbol\")) in want:", "if any(w in norm(r.get(\"name\")) for w in want) or norm(r.get(\"trading_symbol\")) in want:"),
    (X, "index: unordered candles accepted", "if prev is not None and x[\"date\"] <= prev:", "if prev is not None and x[\"date\"] < prev - 1:"),
    (X, "index: staleness ignored", "> MAX_STALE_DAYS:", "> 10 ** 6:"),
    (X, "index: dropping a published index allowed", "if lbl not in {x[\"label\"] for x in entries}:", "if False:"),
    (X, "index: zero volume kept", "v if isinstance(v, int) and v > 0 else None", "v if isinstance(v, int) and v >= 0 else None"),
    (X, "index: key change mixes histories", "prior = []                                      # the key changed: never mix two instruments", "pass"),
    (X, "index: duplicate instrument allowed", "if key in used:", "if False:"),
]


def run(i, m):
    f, name, old, new = m
    src = (ROOT / f).read_text()
    if old not in src:
        return i, name, "PATTERN NOT FOUND"
    tmp = Path(tempfile.mkdtemp())
    try:
        for x in FILES:
            shutil.copy(ROOT / x, tmp / x)
        (tmp / f).write_text(src.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "-f", "test_market_data", "test_index_data"], cwd=tmp, capture_output=True, text=True, timeout=600)
        return i, name, "killed" if r.returncode != 0 else "SURVIVED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    bad = 0
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        results = sorted(ex.map(lambda a: run(*a), enumerate(M)))
    for i, name, res in results:
        print("%-9s %s" % (res, name))
        bad += res != "killed"
    print("\n%d mutants, %d killed, %d not killed" % (len(M), len(M) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
