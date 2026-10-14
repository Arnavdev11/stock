"""
Shared by the protected-file guard tests. The workflow files are protected, with exactly the exceptions recorded in tests/approved_workflow_changes.json:
  * .github/workflows/update.yml may differ from HEAD only by the listed added lines (guarded copies into the site folder; an empty difference is fine);
  * .github/workflows/historical.yml and .github/workflows/save_historical.yml must be byte-for-byte the pinned versions (SHA-256).
is_approved(root, path) is True only for those exact states. Any other edit, or any other file, is False.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

_CFG = json.loads((Path(__file__).parent / "tests" / "approved_workflow_changes.json").read_text(encoding="utf-8"))
PATH = _CFG["update_yml"]["path"]
ADDED = _CFG["update_yml"]["added_lines"]
PINNED = _CFG["pinned_files"]
UPDATERS = _CFG["updater_changes"]            # {path: the exact diff lines (with their + / - prefix) against HEAD}
APPROVED_PATHS = [PATH] + list(PINNED) + list(UPDATERS)


def approved_change_only(root):
    r = subprocess.run(["git", "diff", "-U0", "HEAD", "--", PATH], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    lines = [l for l in r.stdout.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return lines == ["+" + a for a in ADDED]


def exact_diff(root, path, expected):
    r = subprocess.run(["git", "diff", "-U0", "HEAD", "--", path], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    return [l for l in r.stdout.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))] == expected


def is_approved(root, path):
    if path == PATH:
        return approved_change_only(root)
    if path in UPDATERS:
        return exact_diff(root, path, UPDATERS[path])
    if path in PINNED:
        try:
            return hashlib.sha256((Path(root) / path).read_bytes()).hexdigest() == PINNED[path]
        except OSError:
            return False
    return False


# ---------------------------------------------------------------- index.html: the one approved edit to the Stock Detail header (Market Cap replaces the Sector tile)
def without_market_cap(html):
    """index.html with exactly the approved header edit reversed (the loader's fourth request, the marketCap() helper, the Market Cap tile, its variable and its note).
    A page that does not carry the edit comes back unchanged, so the guards can apply this to both the working file and HEAD and then compare them byte for byte:
    any OTHER difference in index.html is still a difference."""
    html = re.sub(r'<script type="module" id="stocklens-quarterly">[\s\S]*?</script>\n', "", html, count=1)      # the read-only Quarterly Results section module is an addition with its own tests (tests/test_quarterly_ui.js)
    s = re.sub(r"/\* Market capitalisation for the header = [\s\S]*?return out\}\n\n", "", html, count=1)
    s = s.replace('j("out/historical.json"),j("out/shareholding.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2],sh:a[3]}});',
                  'j("out/historical.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2]}});', 1)
    s = s.replace("  var company=v.company_name||f.company_name,mc=marketCap(d.sh,s,p.price);\n", "  var company=v.company_name||f.company_name,sector=v.sector;\n", 1)
    s = s.replace('tile("Market Cap (\u20b9 crore)",cr(mc.cap))', 'tile("Sector",sector?esc(sector):"-")', 1)
    s = re.sub(r" '\+\(mc\.cap!==null\?[\s\S]*?\)\+'</p>';", "</p>';", s, count=1)
    return without_index_tabs(s)


# ---------------------------------------------------------------- index.html: the approved NIFTY 50 / SENSEX tabs in the Market Today panel
_TABS_NEW_HEAD = (
    'var INDEX_TABS=["NIFTY 50","SENSEX"];\nS_["mk-today"]=function(m,st){\n'
    "  var h='',sel=INDEX_TABS.indexOf(st&&st.index)>=0?st.index:INDEX_TABS[0];\n")
_TABS_OLD_HEAD = 'S_["mk-today"]=function(m){\n  var h=\'\';\n'
_TABS_BAR = re.compile(r"  h\+='<div class=\"tabs mk-tabs\"[\s\S]*?  if\(sel===\"SENSEX\"\)return h\+[^\n]*;\n")
_SENSEX_EOD = re.compile(r"[ \t]*/\* SENSEX-EOD:start \*/[\s\S]*?/\* SENSEX-EOD:end \*/\n")


# ---------------------------------------------------------------- index.html: the approved stale-NIFTY 50 notice in the Market Today panel
_STALE_NEW = ("""      +'<p class="mk-sub">As of '+esc(x.perf.date)+'</p>'\n"""
              """      +(isDay(m.asOf)&&isDay(x.perf.date)&&dayMs(m.asOf)>dayMs(x.perf.date)?'<p class="mk-sub mk-stale">Stale: this NIFTY 50 value is from <b>'+esc(x.perf.date)+'</b>, older than the market data dated <b>'+esc(m.asOf)+'</b>. It is the last value available and is not current.</p>':'')\n"""
              """      +'</div>'}).join("")+'</div>';""")
_STALE_OLD = """      +'<p class="mk-sub">As of '+esc(x.perf.date)+'</p></div>'}).join("")+'</div>';"""


def without_nifty_stale_notice(html):
    """index.html with exactly the approved stale-NIFTY notice reversed. A page without any trace of it comes back unchanged; a page with part of it, or with it twice, raises ValueError."""
    n = html.count(_STALE_NEW)
    marks = ("Stale: this NIFTY 50 value is from", "mk-stale", "older than the market data dated")
    if n == 0 and not any(m in html for m in marks):
        return html
    if n != 1 or any(html.count(m) != 1 for m in marks):
        raise ValueError("stale-NIFTY notice expected exactly once, found %d" % n)
    return html.replace(_STALE_NEW, _STALE_OLD, 1)


def without_index_tabs(html):
    """index.html with exactly the approved Market Today tab edit reversed (the INDEX_TABS list, the tab bar, the SENSEX branch of S_["mk-today"] and the marked SENSEX-EOD blocks).
    A page that does not carry the edit comes back unchanged. Used like without_market_cap: apply to both sides, then compare byte for byte."""
    html = without_nifty_stale_notice(html)
    html = _SENSEX_EOD.sub("", html)           # the SENSEX end-of-day card: its constants and functions, and the lazy file request (both marked SENSEX-EOD)
    if _TABS_NEW_HEAD not in html:
        return without_market_activity(html)
    s = html.replace(_TABS_NEW_HEAD, _TABS_OLD_HEAD, 1)
    return without_market_activity(_TABS_BAR.sub("", s, count=1))


# ---------------------------------------------------------------- index.html: the approved Stage 8C market-activity edit (52-week lows, Most Active, honest All-Time wording)
_ACT_BLOCKS = (            # (first line, last line): two inserted blocks, each removed whole
    ("\n/* ---------- out/market_activity.json (written by market_data_updater.py", '  return"the validated market-activity file cannot be used"}\n'),
    ("/* All-time high / low: no calculation exists.", "not live.</p>'}\n"),
)
_ACT_BLOCK_SHA256 = (      # sha256 of each approved block, exactly as inserted (first line to last line, newlines included): any edit inside a block changes it
    "fcd64ee5025273356e725fd4510ccbdf76f8f27e8b57fcae05da032e2d335a09",
    "eb70dca8043e750efa9869299081e1a54fb82943ed336f12125966a884749701",
)
_ACT_EDITS = (             # (Stage 8C text, original text): each must occur exactly once
    ("""'<h4 class="mk-h4">New 52-week low</h4>'+lowBody(m);""", """'<h4 class="mk-h4">New 52-week low</h4>'+na(NEED_ALL);"""),
    ('  else if(tab==="ath")h+=na(ATH_WHY);\n  else h+=activeBody(m);\n',
     '  else if(tab==="ath")h+=na("needs the full price history of every stock (the current data holds about one year)");\n  else h+=na("most-active needs traded volume for all NSE stocks");\n'),
    ('card("New 52-Week Low","Close below the previous 52-week low",lowBody(m))', 'card("New 52-Week Low","",na(NEED_ALL+" (the scan lists new highs only)"))'),
    ('card("New All-Time High","",na(ATH_WHY))+card("New All-Time Low","",na(ATH_WHY))',
     'card("New All-Time High","",na("needs the full price history of every stock"))+card("New All-Time Low","",na("needs the full price history of every stock"))'),
    ("function model(S,H,F,X,Y,A){", "function model(S,H,F,X,Y){"),
)
_ACT_EDITS = _ACT_EDITS + (
    ("sectorStocks:Y||null,S:S,asOf:S&&isDay(S.as_of)?S.as_of:null,act:activityOf(A,S&&isDay(S.as_of)?S.as_of:null),indices:",
     "sectorStocks:Y||null,S:S,asOf:S&&isDay(S.as_of)?S.as_of:null,indices:"),
    ('getJson("out/market_sectors.json"),getJson("out/market_activity.json")]).then(function(r){M=model(r[0],r[1],r[2],r[3],null,r[4]);',
     'getJson("out/market_sectors.json")]).then(function(r){M=model(r[0],r[1],r[2],r[3]);'),
    ("window.SLMarket={activityOf:activityOf,perfOf:", "window.SLMarket={perfOf:"),
)


def without_market_activity(html):
    """index.html with exactly the approved Stage 8C edit reversed. A page without any trace of it comes back unchanged (so the guards can apply this to both the working
    file and HEAD). A page that carries SOME of it, or carries any piece twice, raises ValueError: nothing is ever removed on a guess."""
    present = [html.count(a) for a, _ in _ACT_EDITS]
    present += [html.count(first) for first, _ in _ACT_BLOCKS]
    if not any(present):
        return html
    s = html
    for (first, last), pin in zip(_ACT_BLOCKS, _ACT_BLOCK_SHA256):
        if s.count(first) != 1:
            raise ValueError("Stage 8C block start expected exactly once, found %d: %r" % (s.count(first), first[:60]))
        i = s.index(first)
        j = s.find(last, i)
        if j < 0 or s.count(last) != 1:
            raise ValueError("Stage 8C block end expected exactly once: %r" % last[:60])
        end = j + len(last)
        if hashlib.sha256(s[i:end].encode("utf-8")).hexdigest() != pin:
            raise ValueError("Stage 8C block differs from the approved content (sha256 mismatch): %r" % first[:60])
        s = s[:i] + s[end:]
    for new, old in _ACT_EDITS:
        if s.count(new) != 1:
            raise ValueError("Stage 8C edit expected exactly once, found %d: %r" % (s.count(new), new[:70]))
        s = s.replace(new, old, 1)
    return s
