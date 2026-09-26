"""Flag absolute or superlative language for a human to review. Never judges truth."""
from __future__ import annotations
import re
from .claims import sentences
from .report import Finding, FLAGGED

# A priority claim is a construction, not the word "first". The old rule was
# an exclusion list of nouns; that is why "first hidden layer" and
# sentence-initial "First," kept firing.
#
# "attempt" was in the noun list to catch "the first attempt to prove X", but
# it also fired on "on the first attempt the model made a mistake" -- an
# ordinal, not a priority claim. It is now allowed only followed by "to",
# so the priority reading survives and the ordinal reading does not.
# Intervening tokens between "first" and the target noun cannot themselves be
# determiners: "the first attempt the model takes" was matching as
# adjective + noun and firing on ordinary ordinal usage. The negative
# lookahead blocks that reading without losing "the first well-known method".
_INTERV = r"(?!the\b|a\b|an\b|of\b|for\b|on\b|to\b)\w+"
_PRIORITY = re.compile(
    r"(?:we\s+are\s+the\s+first|"
    r"this\s+is\s+the\s+first|"
    r"the\s+first\s+(?:to|that|which)\b|"
    r"the\s+first(?:\s+" + _INTERV + r"){0,2}\s+"
    r"(?:work|paper|study|method|model|approach|system)\b|"
    r"the\s+first(?:\s+" + _INTERV + r"){0,2}\s+attempt\s+to\b|"
    r"for\s+the\s+first\s+time|"
    r"(?:are|is|was|were)\s+the\s+first\s+to\b)",
    re.I,
)


def _is_priority(sent, m):
    if not sent[:m.start()].strip() and re.match(r"first\s*,", sent[m.start():], re.I):
        return False
    if re.match(r"first\s+\d", sent[m.start():], re.I):
        return False
    return bool(_PRIORITY.search(sent))


# Superiority: absolute/universal forms only. A comparative with a table
# behind it ("outperforms several baselines", "SOTA on eleven tasks") is
# ordinary results language, not an overclaim this check can judge.
_UNIV_OBJ = re.compile(
    r"\s+(?:all|every|any|everything|everyone)(?:\s+existing)?\b", re.I)
_BEST_ABS = re.compile(
    r"(?:\s+\w+){0,3}\s+ever\b|\s+(?:in\s+the\s+world|of\s+all(?:\s+time)?|known)\b",
    re.I)


def _is_superiority(sent, m):
    token = re.sub(r"[\s-]+", "", m.group(0).lower())
    rest, before = sent[m.end():], sent[:m.start()]
    if token.startswith(("beat", "outperform", "surpass")):
        return bool(_UNIV_OBJ.match(rest))
    if token == "best":
        if re.search(r"\bat\s+$", before, re.I):
            return False
        if re.match(r"\s+(?:judgment|judgement|practices|effort|"
                    r"of\s+our\s+knowledge)\b", rest, re.I):
            return False
        return bool(_BEST_ABS.match(rest))
    return False


# A "Proof." on its own is a theorem-block header, not a certainty claim.
# The stress test in tests/test_paper_validation.py section G recorded 15
# such false positives from one arXiv math paper before this gate landed;
# "In order to prove (ii)" and "This proves (i)" inside prose still fire.
_PROOF_HEADING = re.compile(r"^\s*proof\s*[.:]?\s*$", re.I)


def _is_proof_language(sent, m):
    return not _PROOF_HEADING.match(sent)


PATTERNS = [
    (r"\bfirst\b", "priority claim", _is_priority),
    # "only one per block" is a quantity, not exclusivity.
    (r"\b(?:only\s+(?:known|way|publisher|method|tool)|"
     r"only\s+one\s+(?:that|which|to|who))\b", "exclusivity claim", None),
    (r"\b(?:nobody|no\s?one|no other|none of)\b", "universal negative", None),
    (r"\b(?:proven|proves?|proof|prove)\b", "proof language", _is_proof_language),
    (r"\b(?:always|never)\b", "absolute", None),
    (r"\b(?:beats?|outperforms?|surpass(?:es)?|best|state[- ]of[- ]the[- ]art|sota)\b",
     "superiority claim", _is_superiority),
    (r"\b(?:novel|groundbreaking|revolutionary|unprecedented|breakthrough)\b",
     "novelty claim", None),
    (r"\b(?:guarantee[sd]?|guaranteed)\b", "guarantee", None),
    (r"\b(?:clearly|obviously|undeniably|definitely|certainly)\b", "certainty word", None),
    (r"\b(?:everyone|everybody|all\s+users|any\s+model)\b", "universal claim", None),
]
STRICT = [(r"\b(?:every|all)\b", "universal quantifier (noisy)", None)]
# Articles are allowed between "not" and the target because "not the only way"
# and "not a proof" are common negation patterns and were missed by the older
# linking-verbs-only whitelist.
NEG = re.compile(r"(?:\bnot|\bno|n't|\bnever|\bwithout|\bnor)\s+(?:(?:be|been|is|are|was|were|yet|really|always|necessarily|fully|the|a|an)\s+)*$", re.I)


def scan(files, strict=False):
    pats = [(re.compile(p, re.I), why, gate) for p, why, gate in PATTERNS + (STRICT if strict else [])]
    out = []
    for rel, _abs, text in files:
        for line, sent in sentences(text, rel):
            for rx, why, gate in pats:
                for m in rx.finditer(sent):
                    if gate and not gate(sent, m):
                        continue
                    negated = bool(NEG.search(sent[:m.start()]))
                    note = f"{why}: '{m.group(0)}'" + (" (negated)" if negated else "")
                    snip = sent if len(sent) <= 160 else sent[max(0, m.start() - 70):m.end() + 70]
                    out.append(Finding("overclaim", FLAGGED, rel, line, note, snip.strip()))
    return out
