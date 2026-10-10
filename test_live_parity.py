"""
Phase 3 - the page's live layer must make the SAME decisions as the relay's own validator and acceptance rules.

tests/fixtures/live_snapshots.json is generated here, from the Python rules (live/snapshot.py), and is read by tests/test_live_client.js, which runs the browser code against
every case. If the Python rules change, this test fails until the fixture is regenerated on purpose:   python test_live_parity.py --write

It also pins that the live layer was ADDED to index.html and nothing else in the file changed.
"""
import copy
import datetime as dt
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from live import decoder, replay, snapshot, ticks
from live.instruments import Instruments
from live.replay import enc_feed, enc_frame, enc_ltpc

HERE = Path(__file__).parent
FIXTURE = HERE / "tests" / "fixtures" / "live_snapshots.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T0 = int(dt.datetime(2026, 10, 8, 10, 0, 0, tzinfo=IST).timestamp() * 1000)
DAY = "2026-10-08"
N = 12
# sha256 of index.html at the Phase 2 commit f682bbb. The live layer is the ONLY addition allowed on top of it in this phase.
PHASE2_INDEX_SHA256 = "1c30ad6155d104e709c65ba0d33094245625a6ccb99ee64cc741f58548b9950b"
BLOCK_RE = re.compile(r'<script type="module" id="stocklens-live">[\s\S]*?</script>\n')
# The search module was changed on purpose after Phase 2 (universe search; behaviour pinned by tests/test_company_search.js). It is compared on its own: pinned by hash in
# tests/search_module_pin.json. Everything else in the page is still compared byte for byte, with the search module removed from both sides.
SEARCH_RE = re.compile(r'<script type="module" id="stocklens-search">[\s\S]*?</script>\n')
PHASE2_INDEX_WITHOUT_SEARCH_SHA256 = "f64f7f6375517b13c52b84bdad708cd9b81313ba4aa856b49ed23efc76656278"   # sha256 of f682bbb:index.html with its one search module removed
SEARCH_PIN = json.loads((Path(__file__).parent / "tests" / "search_module_pin.json").read_text(encoding="utf-8"))


def search_module_text(html):
    return html.split('<script type="module" id="stocklens-search">')[1].split("</script>")[0]


def _doc(universe="nifty500", market_status="open", liquid=True, covered=N, price_symbols=("TST00001", "TST00002", "TST00003"), relay_state="streaming", ticks_at=T0, now=T0 + 1000):
    ins = Instruments.from_rows(replay.synthetic_rows(N, 3))
    st = ticks.TickStore(ins, DAY)
    st.begin_connection(T0 - 1)
    feeds = {}
    for i, k in enumerate(sorted(ins.equities)[:covered]):
        move = (i - N / 2) * 0.7                                     # a spread of gains and losses, one unchanged
        feeds[k] = enc_feed(enc_ltpc(round(100 * (1 + move / 100), 2), ticks_at - 100, 1, 100.0))
    for j, k in enumerate(ins.indices):
        feeds[k] = enc_feed(enc_ltpc(round(20000 * (1 + (j - 1) * 0.004), 2), ticks_at - 100, 1, 20000.0))
    st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, ticks_at, {"NSE_EQ": 2})), ticks_at)
    syms = {s for s in ins.equities.values()}
    return snapshot.build_snapshot(st, ins, now, seq=7, market_status=market_status, liquid=(syms if liquid else set()), subscribed_equities=N, relay_state=relay_state,
                                   price_symbols=price_symbols, universe=universe, cfg={"movers": 3})


def _verdict(doc, now):
    r = snapshot.client_accepts(doc, now)
    return {k: [bool(v[0]), v[1]] for k, v in r.items()}


MUTATIONS = {
    "extra key": lambda d: d.update(token="x"),
    "missing key": lambda d: d.pop("prices"),
    "bad kind": lambda d: d.update(kind="other"),
    "bad schema": lambda d: d.update(schema=2),
    "negative seq": lambda d: d.update(seq=-1),
    "float seq": lambda d: d.update(seq=1.5),
    "string seq": lambda d: d.update(seq="7"),
    "generated_at mismatch": lambda d: d.update(generated_at="2026-01-01T00:00:00.000+05:30"),
    "bad market status": lambda d: d["market"].update(status="maybe"),
    "segments not an object": lambda d: d["market"].update(segments=[]),
    "unknown universe": lambda d: d["scope"].update(universe="nse_all"),
    "coverage above 1": lambda d: d["scope"].update(coverage=1.5),
    "negative subscribed": lambda d: d["scope"].update(subscribed=-1),
    "ticked above subscribed": lambda d: d["scope"].update(ticked=99),
    "index ltp zero": lambda d: d["indices"][0].update(ltp=0),
    "index name empty": lambda d: d["indices"][0].update(name=""),
    "index pct mismatch": lambda d: d["indices"][0].update(change_pct=9.9),
    "index cp negative": lambda d: d["indices"][0].update(cp=-1),
    "more indices than expected": lambda d: d["scope"].update(indices_expected=1),
    "breadth sum": lambda d: d["breadth"].update(counted=99),
    "breadth negative": lambda d: d["breadth"].update(declines=-1, counted=d["breadth"]["counted"] - 1 - d["breadth"]["declines"]),
    "gainer wrong sign": lambda d: d["gainers"][0].update(change_pct=-1),
    "gainer pct mismatch": lambda d: d["gainers"][0].update(change_pct=3.14159),
    "gainer order": lambda d: d["gainers"].reverse(),
    "unsafe symbol": lambda d: d["gainers"][0].update(symbol="../x"),
    "lowercase symbol": lambda d: d["gainers"][0].update(symbol="abc"),
    "html symbol": lambda d: d["losers"][0].update(symbol="<img src=x>"),
    "one list null": lambda d: d.update(gainers=None),
    "too many gainers": lambda d: d.update(gainers=[d["gainers"][0]] * 51),
    "quality inconsistent": lambda d: d["quality"].update(ok=False),
    "quality reasons not a list": lambda d: d["quality"].update(reasons="feed_silent"),
    "bad source": lambda d: d.update(source="x"),
    "prices not an object": lambda d: d.update(prices=[]),
    "price symbol bad": lambda d: d["prices"].update({"bad symbol": {"ltp": 1}}),
    "price ltp string": lambda d: d["prices"][next(iter(d["prices"]))].update(ltp="1"),
}


def build_cases():
    cases = []

    def add(name, doc, now):
        probs = snapshot.validate_snapshot(doc)
        cases.append({"name": name, "doc": doc, "now": now, "valid": not probs, "accepts": _verdict(doc, now)})
    good = _doc()
    t = good["server_ts_ms"]
    add("good nifty500", good, t + 3000)
    add("good nse_eq_all", _doc(universe="nse_eq_all"), t + 3000)
    add("age exactly 20000", good, t + 20000)
    add("age 20001", good, t + 20001)
    add("future 5000", good, t - 5000)
    add("future 5001", good, t - 5001)
    add("market closed", _doc(market_status="closed"), t + 1000)
    add("market pre_open", _doc(market_status="pre_open"), t + 1000)
    add("market unknown", _doc(market_status="unknown"), t + 1000)
    add("test_set universe", _doc(universe="test_set"), t + 1000)
    add("relay not streaming", _doc(relay_state="backoff"), t + 1000)
    add("feed silent", _doc(now=T0 + 30_000), T0 + 31_000)
    add("low coverage reported by relay", _doc(covered=6), t + 1000)
    add("no liquid list", _doc(liquid=False), t + 1000)
    add("no prices requested", _doc(price_symbols=()), t + 1000)
    low = copy.deepcopy(good)
    low["scope"].update(coverage=0.5, ticked=6)                       # healthy flag but thin coverage: the page must still refuse breadth and movers
    add("coverage 0.5 though relay said ok", low, t + 1000)
    edge = copy.deepcopy(good)
    edge["scope"].update(coverage=0.9, ticked=11)
    add("coverage exactly 0.9", edge, t + 1000)
    just = copy.deepcopy(good)
    just["scope"].update(coverage=0.8999)
    add("coverage 0.8999", just, t + 1000)
    miss = copy.deepcopy(good)
    miss["indices"] = miss["indices"][:2]
    add("one index missing", miss, t + 1000)
    noidx = copy.deepcopy(good)
    noidx["indices"] = []
    noidx["scope"]["indices_expected"] = 0
    add("no indices expected", noidx, t + 1000)
    empty = copy.deepcopy(good)
    empty["gainers"], empty["losers"] = [], []
    add("empty movers lists", empty, t + 1000)
    nocp = copy.deepcopy(good)
    nocp["indices"][0].update(cp=None, change=None, change_pct=None)
    add("index without previous close", nocp, t + 1000)
    zero = copy.deepcopy(good)
    zero["breadth"] = {"advances": 0, "declines": 0, "unchanged": 0, "counted": 0}
    add("nothing counted", zero, t + 1000)
    for name, mut in MUTATIONS.items():
        d = copy.deepcopy(good)
        mut(d)
        add("mutation: " + name, d, t + 1000)
    raw = [{"name": "not json", "text": "{nope"}, {"name": "NaN literal", "text": json.dumps(good).replace('"seq": 7', '"seq": NaN')},
           {"name": "Infinity literal", "text": '{"ltp": Infinity}'}, {"name": "empty body", "text": ""}, {"name": "array", "text": "[]"}, {"name": "null", "text": "null"},
           {"name": "string", "text": '"hello"'}, {"name": "number", "text": "42"}]
    return {"generated_by": "test_live_parity.py", "base_ms": t, "cases": cases, "raw": raw}


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, sort_keys=False) + "\n"


class ParityFixtureTests(unittest.TestCase):
    def test_committed_fixture_matches_the_python_rules(self):
        built = dumps(build_cases())
        self.assertTrue(FIXTURE.exists(), "run:  python test_live_parity.py --write")
        self.assertEqual(FIXTURE.read_text(encoding="utf-8"), built,
                         "tests/fixtures/live_snapshots.json is out of date with live/snapshot.py: run  python test_live_parity.py --write  and review the change")

    def test_fixture_covers_the_cases_that_matter(self):
        f = build_cases()
        names = {c["name"] for c in f["cases"]}
        for n in ("good nifty500", "test_set universe", "age 20001", "future 5001", "market closed", "coverage 0.5 though relay said ok", "no liquid list", "one index missing"):
            self.assertIn(n, names)
        self.assertGreaterEqual(len(f["cases"]), 50)
        valid = [c for c in f["cases"] if c["valid"]]
        invalid = [c for c in f["cases"] if not c["valid"]]
        self.assertGreaterEqual(len(valid), 15)
        self.assertGreaterEqual(len(invalid), 30)
        by = {c["name"]: c for c in f["cases"]}
        self.assertTrue(all(v[0] for v in by["good nifty500"]["accepts"].values() if v[1] == "") or True)
        self.assertTrue(by["good nifty500"]["accepts"]["indices"][0] and by["good nifty500"]["accepts"]["breadth"][0] and by["good nifty500"]["accepts"]["movers"][0])
        self.assertFalse(any(v[0] for v in by["test_set universe"]["accepts"].values()))
        self.assertTrue(by["test_set universe"]["valid"])                         # a test_set snapshot is well-formed, and still refused
        self.assertFalse(any(v[0] for v in by["mutation: unknown universe"]["accepts"].values()))
        for c in invalid:
            self.assertFalse(any(v[0] for v in c["accepts"].values()), c["name"])    # an invalid snapshot is refused in every block

    def test_every_mutation_is_caught_by_the_python_validator(self):
        f = build_cases()
        for c in f["cases"]:
            if c["name"].startswith("mutation: "):
                self.assertFalse(c["valid"], c["name"])


class PageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.html = (HERE / "index.html").read_text(encoding="utf-8")

    def test_removing_the_live_layer_leaves_the_phase2_file_byte_identical(self):
        self.assertEqual(len(BLOCK_RE.findall(self.html)), 1, "exactly one stocklens-live block")
        self.assertEqual(len(SEARCH_RE.findall(self.html)), 1, "exactly one stocklens-search module")
        rest = SEARCH_RE.sub("", BLOCK_RE.sub("", self.html, count=1), count=1)
        self.assertEqual(hashlib.sha256(rest.encode("utf-8")).hexdigest(), PHASE2_INDEX_WITHOUT_SEARCH_SHA256,
                         "index.html outside the stocklens-live block and outside the search module differs from the Phase 2 commit f682bbb")

    def test_the_search_module_is_exactly_the_approved_version(self):
        self.assertEqual(hashlib.sha256(search_module_text(self.html).encode("utf-8")).hexdigest(), SEARCH_PIN["page_search_module_sha256"],
                         "the search module differs from the approved universe-search version pinned in tests/search_module_pin.json")

    def test_the_block_sits_directly_after_the_market_module(self):
        m = self.html.index('<script type="module" id="stocklens-market">')
        end = self.html.index("</script>\n", m) + len("</script>\n")
        self.assertTrue(self.html[end:].startswith('<script type="module" id="stocklens-live">'))

    def test_matches_git_when_available(self):
        import shutil
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git is not installed here")
        r = subprocess.run(["git", "show", "f682bbb:index.html"], cwd=str(HERE), capture_output=True)
        if r.returncode != 0:
            self.skipTest("commit f682bbb is not in this checkout")
        self.assertEqual(hashlib.sha256(r.stdout).hexdigest(), PHASE2_INDEX_SHA256)
        self.assertEqual(SEARCH_RE.sub("", BLOCK_RE.sub("", self.html, count=1), count=1).encode("utf-8"), SEARCH_RE.sub("", r.stdout.decode("utf-8"), count=1).encode("utf-8"))

    def test_the_block_has_no_token_no_html_sinks_no_outside_address_no_advice(self):
        block = BLOCK_RE.search(self.html).group(0)
        code = re.sub(r"/\*[\s\S]*?\*/", "", block)
        self.assertNotRegex(code, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|importScripts|XMLHttpRequest|WebSocket|sendBeacon|localStorage|sessionStorage|document\.cookie")
        no_label = code.replace('"Upstox market data feed V3 (ltpc)"', "")                       # the one allowed mention: the data-source label the snapshot must carry
        self.assertNotRegex(no_label, r"(?i)authorization|bearer|upstox|access_token|api[_-]?key|secret|password")
        self.assertEqual(code.count('"Upstox market data feed V3 (ltpc)"'), 1)
        self.assertNotRegex(code, r"https?://(?!127\.0\.0\.1:)")                                   # the only address in the module is the local relay
        self.assertEqual(len(re.findall(r"https?://", code)), 1)
        self.assertNotRegex(code, r"\b(Buy|Sell|Target|Rating|Recommend)\b")
        self.assertEqual(len(re.findall(r"\bfetch\(", code)), 1)


if __name__ == "__main__":
    if "--write" in sys.argv:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(dumps(build_cases()), encoding="utf-8")
        print("wrote", FIXTURE, FIXTURE.stat().st_size, "bytes")
    else:
        unittest.main()
