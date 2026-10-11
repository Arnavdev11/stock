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
    s = re.sub(r"/\* Market capitalisation for the header = [\s\S]*?return out\}\n\n", "", html, count=1)
    s = s.replace('j("out/historical.json"),j("out/shareholding.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2],sh:a[3]}});',
                  'j("out/historical.json")]).then(function(a){return{val:a[0],fin:a[1],prices:a[2]}});', 1)
    s = s.replace("  var company=v.company_name||f.company_name,mc=marketCap(d.sh,s,p.price);\n", "  var company=v.company_name||f.company_name,sector=v.sector;\n", 1)
    s = s.replace('tile("Market Cap (\u20b9 crore)",cr(mc.cap))', 'tile("Sector",sector?esc(sector):"-")', 1)
    return re.sub(r" '\+\(mc\.cap!==null\?[\s\S]*?\)\+'</p>';", "</p>';", s, count=1)
