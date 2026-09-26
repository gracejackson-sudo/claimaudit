"""Record real sources for the citation-support tests.

Fetches each source through the SAME code path a user's run takes
(support.NetworkProvider), then stores what the checks need and nothing more:
metadata, the abstract, the extraction's self-check numbers, and a bag of token
counts. Not the text. A bag of words is enough to ask "is this word there" and
cannot be turned back into the paper.

    python validation/build_support_fixtures.py

Needs the network. The tests never do.
"""
import gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))
from claimaudit import support

OUT = os.path.join(HERE, os.pardir, "fixtures", "citation_support", "records")
IDS = ["arxiv:2606.24020", "arxiv:2606.01850", "arxiv:2403.03868", "arxiv:2210.17323",
       "arxiv:2211.10438", "arxiv:2204.12852", "arxiv:2411.04330", "arxiv:1604.04173",
       "arxiv:2202.13415", "arxiv:1209.2673",
       "doi:10.1214/23-AOS2276", "doi:10.1080/01621459.2017.1307116", "doi:10.1007/b106715"]


def main():
    os.makedirs(OUT, exist_ok=True)
    import time
    prov = support.NetworkProvider()
    for ident in IDS:
        s = prov(ident)
        for wait in (60, 120, 240):       # arXiv throttles bursts; a real id is worth waiting for
            if s.tier == "none" and "unavailable" in s.note and ident.startswith("arxiv:"):
                time.sleep(wait)
                prov.memo.pop(ident, None)
                s = prov(ident)
        time.sleep(10)
        rec = {"ident": s.ident, "title": s.title, "authors": s.authors, "year": s.year,
               "abstract": s.abstract, "tier": s.tier, "qual": s.qual, "note": s.note,
               "index": s.index.to_json() if s.index else None}
        fn = os.path.join(OUT, ident.replace(":", "_").replace("/", "_") + ".json.gz")
        with gzip.open(fn, "wt", encoding="utf-8") as fh:
            json.dump(rec, fh)
        print(f"{ident:<36} tier={s.tier:<8} trusted={s.trusted!s:<5} words={s.qual.get('n_words', 0):>6}  {s.note[:60]}")


if __name__ == "__main__":
    main()
