"""
directory_universe.py - turn the PUBLISHED STOCK DIRECTORY into a coverage universe.json (the file every stock-specific updater already reads).

The stock directory (stock_directory.json on the data branch) is the classified, Upstox-served list of eligible NSE EQ and BE stocks, and each entry already has the
universe's shape: symbol, isin, name. This converts it, unchanged, so no updater, page or workflow needs to know about the directory: they keep reading universe.json
through STOCKLENS_UNIVERSE_FILE. The directory must pass its own checks (schema, counts, hash) first, so a damaged directory can never become the universe.
Nothing is downloaded, no token is read, and no sector, price, series or rating is copied.

    python directory_universe.py build --directory stock_directory.json --out universe.json [--as-of YYYY-MM-DD]

The new universe is refused (and any existing universe.json is left as it was) when it has fewer than MIN_DIRECTORY symbols, fails universe.check_doc, or loses more than
10% of the symbols of the universe already at --out (universe.build_problems).
This module is separate from universe.py on purpose: the research updaters and universe.py never import the directory builder (test_stock_directory.Safety).
"""
import datetime as dt
import json
import sys
from pathlib import Path

import stock_directory
import universe

MIN_DIRECTORY = 1000        # fewer symbols than this is a broken directory, not the eligible list
SOURCE = "Published stock directory (stock_directory.json): eligible NSE EQ and BE stocks served by Upstox"


def build_from_directory(directory_doc, as_of, source=SOURCE):
    """The universe document for a directory document. Raises universe.UniverseError when the directory fails its own checks."""
    bad = stock_directory.check_directory_doc(directory_doc)
    if bad:
        raise universe.UniverseError("the stock directory failed its own checks: " + str(bad[0])[:120])
    syms = sorted(({"symbol": x["symbol"], "isin": x["isin"], "name": x["name"]} for x in directory_doc["symbols"]), key=lambda x: x["symbol"])
    return {"schema_version": universe.SCHEMA_VERSION, "kind": universe.KIND, "stage": "directory", "source": source, "as_of": as_of, "count": len(syms), "symbols": syms}


def main(argv):
    if not argv or argv[0] != "build":
        print(__doc__)
        return 2
    opt, i = {}, 1
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            opt[argv[i]] = argv[i + 1]
            i += 2
        else:
            print("::error::missing value for " + argv[i])
            return 2
    try:
        if "--directory" not in opt or "--out" not in opt:
            raise universe.UniverseError("--directory FILE and --out FILE are required")
        as_of = opt.get("--as-of") or dt.date.today().isoformat()
        try:
            ddoc = json.loads(Path(opt["--directory"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise universe.UniverseError("the stock directory is unreadable: " + type(e).__name__)
        doc = build_from_directory(ddoc, as_of)
        out = Path(opt["--out"])
        prev = None
        if out.exists():
            try:
                prev = universe.load_doc(out)
            except universe.UniverseError:
                prev = None
        bad = universe.build_problems(doc, prev, MIN_DIRECTORY)
        if bad:
            raise universe.UniverseError("universe not written: " + bad[0])
        universe.write_doc(out, doc)
        print("universe written: %d symbols (directory); as of %s" % (doc["count"], as_of))
        return 0
    except universe.UniverseError as e:
        print("::error::" + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
