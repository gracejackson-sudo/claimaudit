"""Does passage selection put the right passage in front of the reader? (No model, no key.)

For each Layer B case, fetch the real source through the production path and check
whether the passage a person identified as the relevant one (`gold`) is inside the
passages that would be shown. If it is not, the reader cannot support or narrow the
sentence, however good it is; so this bounds what Layer B can do.
    python validation/retrieval_check.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_cases
from claimaudit import support, judge as jd, fulltext as ft


def contains(windows, gold):
    g = jd._qnorm(gold)
    return any(g in jd._qnorm(t) for _, t in windows)


def main():
    prov = support.NetworkProvider()
    cases = llm_cases.load()
    hits = total = 0
    print(f"{'case':<22}{'source':<20}{'tier':<7}{'trusted':<9}{'windows':<8}{'gold shown':<11}")
    for c in cases:
        s = prov(c["ident"])
        w = jd.select_windows(s, jd.claim_text(llm_cases.pair_for(c)), c.get("context", "")) if s.text else []
        if c.get("gold"):
            total += 1
            ok = contains(w, c["gold"])
            hits += ok
            shown = "yes" if ok else "NO"
        else:
            shown = "n/a"
        print(f"{c['id']:<22}{c['ident'].split(':', 1)[1][:18]:<20}{s.tier:<7}{str(s.trusted):<9}{len(w):<8}{shown:<11}"
              + ("" if s.trusted else "  " + s.note[:70]))
    print(f"\ngold passage reached the reader in {hits} of {total} cases that name one")


if __name__ == "__main__":
    main()
