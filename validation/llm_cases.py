"""Shared loader: the Layer B case list, and a way to build a check-ready Pair from a case."""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))
from claimaudit import support

PATH = os.path.join(HERE, os.pardir, "fixtures", "citation_support", "llm_cases.json")


def load():
    with open(PATH, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def pair_for(case):
    s = case["sentence"]
    return support.Pair(file="case", line=0, text=s, idents=[case["ident"]], keys=[case["id"]], textual=False,
                        named=[], attributive=True, marked=s + " ⟦C:cite:" + case["id"] + "⟧",
                        bib_authors=[], context=case.get("context", ""))
