"""
Shared by the protected-file guard tests. The workflow files are protected, with exactly the exceptions recorded in tests/approved_workflow_changes.json:
  * .github/workflows/update.yml may differ from HEAD only by the listed added lines (guarded copies into the site folder; an empty difference is fine);
  * .github/workflows/historical.yml and .github/workflows/save_historical.yml must be byte-for-byte the pinned versions (SHA-256).
is_approved(root, path) is True only for those exact states. Any other edit, or any other file, is False.
"""
import hashlib
import json
import subprocess
from pathlib import Path

_CFG = json.loads((Path(__file__).parent / "tests" / "approved_workflow_changes.json").read_text(encoding="utf-8"))
PATH = _CFG["update_yml"]["path"]
ADDED = _CFG["update_yml"]["added_lines"]
PINNED = _CFG["pinned_files"]
APPROVED_PATHS = [PATH] + list(PINNED)


def approved_change_only(root):
    r = subprocess.run(["git", "diff", "-U0", "HEAD", "--", PATH], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    lines = [l for l in r.stdout.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return lines == ["+" + a for a in ADDED]


def is_approved(root, path):
    if path == PATH:
        return approved_change_only(root)
    if path in PINNED:
        try:
            return hashlib.sha256((Path(root) / path).read_bytes()).hexdigest() == PINNED[path]
        except OSError:
            return False
    return False
