"""Flag absolute or superlative language for a human to review. Never judges truth."""
from __future__ import annotations
import re
from .claims import sentences
from .report import Finding, FLAGGED

PATTERNS = [
    (r"\bfirst\b(?!\s+(?:step|stage|section|part|half|row|column|order|pass|time)\b)", "priority claim"),
    (r"\bonly\s+(?:one|known|way|publisher|method|tool)\b", "exclusivity claim"),
    (r"\b(?:nobody|no\s?one|no other|none of)\b", "universal negative"),
    (r"\b(?:proven|proves?|proof|prove)\b", "proof language"),
    (r"\b(?:always|never)\b", "absolute"),
    (r"\b(?:beats?|outperforms?|surpass(?:es)?|best|state[- ]of[- ]the[- ]art|sota)\b", "superiority claim"),
    (r"\b(?:novel|groundbreaking|revolutionary|unprecedented|breakthrough)\b", "novelty claim"),
    (r"\b(?:guarantee[sd]?|guaranteed)\b", "guarantee"),
    (r"\b(?:clearly|obviously|undeniably|definitely|certainly)\b", "certainty word"),
    (r"\b(?:everyone|everybody|all\s+users|any\s+model)\b", "universal claim"),
]
STRICT = [(r"\b(?:every|all)\b", "universal quantifier (noisy)")]
NEG = re.compile(r"(?:\bnot|\bno|n't|\bnever|\bwithout|\bnor)\s+(?:(?:be|been|is|are|was|were|yet|really|always|necessarily|fully)\s+)*$", re.I)


def scan(files, strict=False):
    pats = [(re.compile(p, re.I), why) for p, why in PATTERNS + (STRICT if strict else [])]
    out = []
    for rel, _abs, text in files:
        for line, sent in sentences(text, rel):
            for rx, why in pats:
                for m in rx.finditer(sent):
                    negated = bool(NEG.search(sent[:m.start()]))
                    note = f"{why}: '{m.group(0)}'" + (" (negated)" if negated else "")
                    snip = sent if len(sent) <= 160 else sent[max(0, m.start() - 70):m.end() + 70]
                    out.append(Finding("overclaim", FLAGGED, rel, line, note, snip.strip()))
    return out
