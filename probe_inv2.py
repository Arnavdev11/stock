"""Read-only 5H.6 axis check (throwaway branch only). Writes nothing."""
import os, re, sys, traceback
import shareholding_updater as su
from probe_inv import pairs_of, short

WANT = ("CentralGovernment", "IndividualsOrHUF", "InstitutionsForeignPortfolioInvestor", "OtherInstitutions")


def main():
    sym = os.environ["SYM"]
    nse = su.Nse()
    recs, err = nse.index(sym)
    keep, _ = su.select_filings(recs)
    out = []
    for d in sorted(keep, reverse=True):
        q = d.isoformat()
        if q not in ("2026-06-30", "2021-12-31"):
            continue
        rec = keep[d]
        data, used, e = su.fetch_xbrl(nse, rec["xbrl"])
        if data is None:
            out.append("%s fetch failed" % q); continue
        parsed = su.parse_xbrl(data)
        rd = parsed["report_date"] or q
        P = pairs_of(data, rd)
        by = {}
        for (a, m), f in P.items():
            if any(w in a for w in WANT):
                sh = su.fnum(f.get(su.HOLDER_SHARES_EL))
                if sh:
                    by.setdefault(short(a), []).append((sh, (f.get(su.HOLDER_NAME_EL) or "")[:30], f.get(su.HOLDER_PCT_EL), (f.get(su.HOLDER_FLAG_EL) or "")[:10]))
        for a, hs in sorted(by.items()):
            out.append("%s %s n=%d sum=%d :: %s" % (q[2:], a[:30], len(hs), sum(h[0] for h in hs), " ; ".join("%s %d %s %s" % (h[1], h[0], h[2], h[3]) for h in sorted(hs, reverse=True)[:5])))
        rows = {k[0]: r["shares"] for k, r in parsed["rows"].items() if k[1] == rd and re.search("individual|government|foreignportfolio|otherinstitutions", k[0])}
        out.append("%s AGG %s" % (q[2:], "; ".join("%s=%s" % (k[:44], int(v) if v else v) for k, v in sorted(rows.items()))))
    print("::notice title=%s::%s" % (sym, "%0A".join(out)))


try:
    main()
except SystemExit:
    raise
except BaseException:
    print("::notice title=ERR::" + traceback.format_exc().replace("\n", "%0A")[-2500:])
