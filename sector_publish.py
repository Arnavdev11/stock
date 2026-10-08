"""
sector_publish.py - StockLens sector architecture, Phase 1: THE PUBLICATION GATE.

Sector files are PRIVATE by default. They live in private/ (never out/), and nothing in the existing workflows copies private/.
This module is the only place that may put a sector file into the site folder, and only when the approval flag says so.

    SECTOR_PUBLIC_DISPLAY_APPROVED      environment variable; ONLY the exact text "true" (any case) approves. Anything else, or unset, = not approved.
    DEFAULT_APPROVED = False            the built-in default.

    python sector_publish.py publish --site _site     approved: validate, then copy the two sector files to _site/out/ with public_display_approved=true
                                                       not approved: copy nothing AND remove any sector file already present in _site
    python sector_publish.py check --site _site       exit 1 if the site folder contains a sector file while the gate is closed (use as a last step before deploying)

Never published, whatever the flag: the sector master and the ETF list (reference data, kept private).
Enabling publication later = set the variable to true and add `python sector_publish.py publish --site _site` before the upload step. No other change.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import validate_sector_outputs as vso

DEFAULT_APPROVED = False
ENV_FLAG = "SECTOR_PUBLIC_DISPLAY_APPROVED"
PUBLISHABLE = ["market_sectors.json", "market_sector_stocks.json"]
PRIVATE_ONLY = ["sector_master.json", "etf_list.json"]
SECTOR_KINDS = {"sectors", "sector_stocks", "sector_master"}


def approved(env=None):
    env = os.environ if env is None else env
    v = env.get(ENV_FLAG)
    if v is None:
        return DEFAULT_APPROVED
    return str(v).strip().lower() == "true"


def _is_sector_name(name):
    n = name.lower()
    return n in PUBLISHABLE or n in PRIVATE_ONLY or n.startswith("market_sector") or n.startswith("sector_") or "sector_master" in n


def find_sector_files(site):
    """Every file under the site folder that is a sector file by NAME or, whatever its extension, by CONTENT (its declared kind or approval flag near the top)."""
    found = []
    site = Path(site)
    if not site.is_dir():
        return found
    for f in sorted(site.rglob("*")):
        if not f.is_file():
            continue
        if _is_sector_name(f.name):
            found.append(f)
        else:
            try:
                with open(f, "rb") as fh:
                    head = fh.read(8000).decode("utf-8", errors="ignore")
            except OSError:
                continue
            if '"public_display_approved"' in head or any('"kind": "%s"' % k in head or '"kind":"%s"' % k in head for k in SECTOR_KINDS):
                found.append(f)
    return found


def check_site(site, env=None):
    """Problems if sector files are present while the gate is closed. Open gate: only the private-only files are forbidden."""
    ok = approved(env)
    p = []
    for f in find_sector_files(site):
        if f.name.lower() in PRIVATE_ONLY or "sector_master" in f.name.lower():
            p.append("private reference file in the site folder: " + f.name)
        elif not ok:
            p.append("sector file in the site folder while %s is not true: %s" % (ENV_FLAG, f.name))
    return p


def publish(root, site, env=None):
    """Returns (exit_code, message)."""
    site, root = Path(site), Path(root)
    if not approved(env):
        removed = 0
        for f in find_sector_files(site):
            f.unlink()
            removed += 1
        return 0, "sector publication is not approved: nothing was copied (%d sector file(s) removed from the site folder)" % removed
    docs, p = vso.load_dir(root / "private")
    if not p:
        for n in docs:
            docs[n]["public_display_approved"] = True
        p = vso.validate(docs)
    if p:
        return 1, "sector files NOT published: " + p[0]
    out = site / "out"
    out.mkdir(parents=True, exist_ok=True)
    for n, (fname, _) in vso.NAMES.items():
        tmp = out / (fname + ".tmp")
        tmp.write_text(json.dumps(docs[n], indent=1, allow_nan=False, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(out / fname)
    return 0, "sector files published to %s (approved)" % out


def main(argv):
    if not argv or argv[0] not in ("publish", "check"):
        print("usage: sector_publish.py publish|check --site DIR")
        return 2
    site = argv[argv.index("--site") + 1] if "--site" in argv else "_site"
    if argv[0] == "check":
        p = check_site(site)
        for x in p:
            print("::error::" + x)
        if not p:
            print("site folder has no sector file that the gate forbids")
        return 1 if p else 0
    code, msg = publish(os.environ.get("DATA_DIR", "."), site)
    print(("::error::" if code else "") + msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
