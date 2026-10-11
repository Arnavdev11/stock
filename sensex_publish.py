"""
sensex_publish.py - StockLens SENSEX end-of-day data: THE PUBLICATION GATE FOR THE SITE FOLDER (the same idea as sector_publish.py).

The existing "Build site folder" steps copy every out/*.json file into the site folder. If out/sensex_history.json ever exists, that copy would publish it.
This module runs AFTER those copies and BEFORE the Pages artifact is uploaded, and decides what happens to any SENSEX file in the site folder:

    SENSEX_PUBLIC_DISPLAY_APPROVED      environment variable; ONLY the exact text "true" (any case, surrounding spaces ignored) approves.
                                         Anything else - unset, empty, "false", "1", "yes", "True " with other text - means NOT approved.
    PUBLISH_DEFAULT = False             the built-in default.

    python sensex_publish.py guard --site _site     NOT approved: remove every SENSEX file from the site folder, then prove none is left (exit 1 if one cannot be removed)
                                                    approved:     keep ONLY _site/out/sensex_history.json, and only if it is a valid SENSEX file that says
                                                                  public_display_approved === true; anything else (invalid, unapproved file, SENSEX data anywhere
                                                                  else in the site) is removed and the step fails (exit 1)
    python sensex_publish.py check --site _site     read-only: exit 1 if the site folder is not in the state the rules above allow

No SENSEX file in the site folder at all is always fine (exit 0): that is the normal state today. A missing site folder, a bad option or an unreadable
folder is a failure (exit 1 or 2), never a silent pass. Nothing else in the site folder is touched, so NIFTY 50 and every other JSON output copy exactly as before.

This is independent of the page: index.html has its own SENSEX_PAGE_APPROVED gate, and the data file carries its own public_display_approved flag.
All three must be open before a SENSEX value can reach a visitor. NOT YET CONFIRMED: public redistribution rights for BSE SENSEX data (see sensex_data_updater.py);
that is why every gate ships closed. Enabling publication later needs deliberate, reviewed edits - this variable in the build steps is only one of them.

Standard library only; it makes no network call and reads no credential.
"""
import json
import math
import os
import re
import sys
from pathlib import Path

PUBLISH_DEFAULT = False
ENV_FLAG = "SENSEX_PUBLIC_DISPLAY_APPROVED"
ALLOWED_PATH = Path("out") / "sensex_history.json"          # the only place an approved file may sit inside the site folder
KIND = "sensex_history"
SCHEMA_VERSION = 1
KEY = "BSE_INDEX|SENSEX"
LABEL = "SENSEX"
MAX_BYTES = 150 * 1024 * 1024
HEAD_BYTES = 8000
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def approved(env=None):
    """Fail closed: only the exact text "true" (any case) approves."""
    v = (os.environ if env is None else env).get(ENV_FLAG)
    return True if isinstance(v, str) and v.strip().lower() == "true" else PUBLISH_DEFAULT


NAME = re.compile(r"^sensex[_.\-]|sensex_history")          # NOT a bare substring: real stock files such as by_symbol/HDFCSENSEX.json must never be touched


def _is_sensex_name(name):
    return NAME.search(name.lower()) is not None


def _declares_sensex(path):
    """True when the start of a file says it is SENSEX history data, whatever its name or extension."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return False
    return re.search(r'"kind"\s*:\s*"' + KIND + '"', head) is not None or re.search(r'"instrument_key"\s*:\s*"BSE_INDEX\|', head) is not None


def find_sensex_files(site):
    """Every file or link under the site folder that is a SENSEX file by NAME or by what it declares about itself. Raises OSError when the folder cannot be read."""
    site = Path(site)
    found = []
    for dirpath, dirnames, filenames in os.walk(site, followlinks=False):
        for n in sorted(dirnames + filenames):
            p = Path(dirpath) / n
            if p.is_symlink():
                if _is_sensex_name(n):
                    found.append(p)
            elif p.is_file() and (_is_sensex_name(n) or _declares_sensex(p)):
                found.append(p)
    return found


def validate_file(path):
    """-> list of problems with a SENSEX file that would be published (empty list = fit to publish). Strict: the data must say it is approved."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return ["the file is larger than the safety limit"]
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ["the file cannot be read as JSON"]
    if not isinstance(doc, dict):
        return ["the file is not a JSON object"]
    p = []
    if doc.get("kind") != KIND or doc.get("schema_version") != SCHEMA_VERSION:
        p.append("not a SENSEX history file (kind / schema_version)")
    if doc.get("public_display_approved") is not True:
        p.append("the file does not say public_display_approved is true")
    ins = doc.get("instrument")
    if not isinstance(ins, dict) or ins.get("instrument_key") != KEY or ins.get("label") != LABEL:
        p.append("the instrument is not " + KEY)
    cs = doc.get("candles")
    if not isinstance(cs, list) or len(cs) < 2:
        p.append("fewer than two candles")
    else:
        prev = None
        for c in cs:
            ok = (isinstance(c, dict) and isinstance(c.get("date"), str) and DAY.match(c["date"]) is not None
                  and isinstance(c.get("close"), (int, float)) and not isinstance(c.get("close"), bool)
                  and math.isfinite(c["close"]) and c["close"] > 0 and (prev is None or c["date"] > prev))
            if not ok:
                p.append("a candle is malformed or out of order")
                break
            prev = c["date"]
        else:
            if doc.get("last_date") != prev or doc.get("as_of") != prev:
                p.append("last_date / as_of do not match the last candle")
    return p


def _allowed(site, path):
    try:
        return Path(path).relative_to(Path(site)) == ALLOWED_PATH and not Path(path).is_symlink()
    except ValueError:
        return False


def assess(site, env=None):
    """-> (found files, {file: reason it must not be in the site}) for the current approval state. Read-only."""
    found = find_sensex_files(site)
    bad = {}
    if not approved(env):
        for f in found:
            bad[f] = "SENSEX publication is not approved"
        return found, bad
    for f in found:
        if not _allowed(site, f):
            bad[f] = "approved, but a SENSEX file may only sit at " + ALLOWED_PATH.as_posix()
            continue
        why = validate_file(f)
        if why:
            bad[f] = "approved, but the file is not fit to publish: " + "; ".join(why)
    return found, bad


def guard(site, env=None, out=print):
    """Remove what must not be published, then re-check. -> exit code (0 ok, 1 failure)."""
    site = Path(site)
    if not site.is_dir():
        out("::error::sensex_publish: the site folder does not exist: " + str(site))
        return 1
    try:
        found, bad = assess(site, env)
    except OSError as e:
        out("::error::sensex_publish: the site folder could not be read (" + type(e).__name__ + ")")
        return 1
    failed = False
    for f, why in sorted(bad.items()):
        try:
            f.unlink()
            out("sensex_publish: removed %s from the site folder (%s)" % (f.relative_to(site).as_posix(), why))
        except OSError as e:
            out("::error::sensex_publish: could not remove %s (%s)" % (f.name, type(e).__name__))
            failed = True
        if approved(env):
            failed = True                                                    # approved but unfit: loud failure, the file is gone either way
    try:
        _, left = assess(site, env)
    except OSError as e:
        out("::error::sensex_publish: the site folder could not be re-read (" + type(e).__name__ + ")")
        return 1
    if left:
        out("::error::sensex_publish: a SENSEX file is still in the site folder after the guard")
        return 1
    if failed:
        out("::error::sensex_publish: an approved SENSEX file was not fit to publish and was removed")
        return 1
    return 0


def check(site, env=None, out=print):
    site = Path(site)
    if not site.is_dir():
        out("::error::sensex_publish: the site folder does not exist: " + str(site))
        return 1
    try:
        _, bad = assess(site, env)
    except OSError as e:
        out("::error::sensex_publish: the site folder could not be read (" + type(e).__name__ + ")")
        return 1
    for f, why in sorted(bad.items()):
        out("::error::sensex_publish: %s must not be in the site folder (%s)" % (f.name, why))
    return 1 if bad else 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 3 or a[0] not in ("guard", "check") or a[1] != "--site" or not a[2]:
        print("usage: python sensex_publish.py guard|check --site <site folder>", file=sys.stderr)
        return 2
    return (guard if a[0] == "guard" else check)(a[2])


if __name__ == "__main__":
    sys.exit(main())
