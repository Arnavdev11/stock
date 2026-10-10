"""
Shared by the protected-file guard tests. .github/workflows/update.yml is protected, with exactly ONE approved exception: the guarded copy that publishes the searchable stock
directory (out/stock_directory.json) next to the other site files. approved_change_only() is True when the working-tree diff of that file against HEAD adds exactly
those two lines (a comment and the guarded copy) and removes or alters nothing else. Any other edit, or any other line, makes it False.
"""
import subprocess

PATH = ".github/workflows/update.yml"
ADDED = [
    "          # the searchable stock directory (built by stock_directory.py, kept on the data branch). Optional and separate from universe.json, which only the research updaters read.",
    "          if [ -f ledger-branch/stock_directory.json ]; then cp ledger-branch/stock_directory.json _site/out/stock_directory.json; fi",
]


def approved_change_only(root):
    r = subprocess.run(["git", "diff", "-U0", "HEAD", "--", PATH], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    lines = [l for l in r.stdout.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return lines == ["+" + a for a in ADDED]
