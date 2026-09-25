"""Check claims about variation across seeds against the numbers reported.

The ML analogue of GRIM. Where GRIM asks whether a mean is reachable from
an integer count, this asks whether a reported mean, spread and seed count
can describe the same set of runs.

Four things are certain enough to automate:

1. per-run values are listed and the stated mean does not follow from them;
2. per-run values are listed and the stated spread does not follow from them;
3. the stated number of seeds is not the number of values listed;
4. a standard error and a standard deviation are both given and do not
   agree for the stated number of runs.

A fifth is reported but not called an error: when two results with error
bars are described as a significant improvement and the bars overlap,
nothing in the sentence establishes significance. That is a statement about
what the reported numbers can support, not a claim that the result is wrong,
so it is recorded as unverifiable rather than flagged.

Both the sample and population conventions for spread are accepted, because
papers use both and guessing wrong would mean crying wolf. All comparisons
are intervals over the printed precision, so rounding cannot flag anything.
"""
from __future__ import annotations
import math
import re

from . import claims
from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

N = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"
SN = r"[-\u2212+]?(?:" + N + r")"
PM = r"(?:\u00b1|\+/-|\+/\u2212|\+-)"
RUN = r"seeds?|runs?|trials?|replicates?|restarts?|folds?"

MEAN_STD = re.compile(r"(?P<m>" + SN + r")\s*(?:%|pp)?\s*" + PM +
                      r"\s*(?P<s>" + N + r")\s*(?:%|pp)?")
SEED_N = re.compile(r"(?:over|across|from|using|with|of)\s+(?P<n>\d{1,3})\s+(?:" + RUN + r")", re.I)
VALUES = re.compile(r"(?:" + RUN + r")[^:\n]{0,30}?:\s*\(?\s*(?P<vals>" + SN +
                    r"(?:\s*,\s*" + SN + r"){2,})", re.I)
SEM_STD = re.compile(r"standard\s+error[^.]{0,40}?(?P<sem>" + N + r")", re.I)
STD_WORD = re.compile(r"standard\s+deviation[^.]{0,40}?(?P<sd>" + N + r")", re.I)
SIGNIF = re.compile(r"\bsignificant(?:ly)?\b", re.I)
TEST_NAMED = re.compile(r"\b(?:t-?test|wilcoxon|mann-?whitney|bootstrap|permutation|"
                        r"anova|bonferroni|p\s*[<=>]|p-?value)\b", re.I)


def _f(s):
    return float(str(s).replace(",", "").replace("\u2212", "-"))


def _half(raw):
    raw = str(raw)
    dec = len(raw.split(".")[1]) if "." in raw else 0
    return 0.5 * 10 ** (-dec)


def _overlaps(x, y, eps=1e-9):
    return x[0] - eps <= y[1] and y[0] - eps <= x[1]


def _iv(raw):
    v, h = _f(raw), _half(raw)
    return v - h, v + h


def _std(xs, ddof):
    n = len(xs)
    if n - ddof <= 0:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _values(raw):
    return [v.strip() for v in raw.split(",")]


def _are_identifiers(vals):
    """Seed numbers, not measurements.

    "seeds: 0, 1, 2" lists which seeds were used. A metric is virtually
    always written with a decimal point, so requiring one is a cheap and
    defensible way to tell the two apart.
    """
    return all("." not in v for v in vals)


def check(tfiles):
    out = []
    for rel, _abs, text in tfiles:
        for line, sent in claims.sentences(text, rel):
            clean = claims._URLISH.sub(" ", sent)
            out += _sentence(rel, line, clean)
    return out


def _sentence(rel, line, clean):
    out = []
    vm = VALUES.search(clean)
    ms = MEAN_STD.search(clean)
    sn = SEED_N.search(clean)

    if vm:
        raws = _values(vm.group("vals"))
        if not _are_identifiers(raws):
            xs = [_f(r) for r in raws]
            h = max(_half(r) for r in raws)
            n = len(xs)

            # 3. the seed count must be the number of values listed
            if sn and int(sn.group("n")) != n:
                out.append(Finding("seeds", FLAGGED, rel, line,
                                   f"{sn.group('n')} {('runs')} are claimed but {n} "
                                   f"value(s) are listed",
                                   "one of the two is wrong"))

            if ms:
                # 1. the stated mean must follow from the values
                m_raw, s_raw = ms.group("m"), ms.group("s")
                got = sum(xs) / n
                m_iv = (got - h, got + h)
                if _overlaps(_iv(m_raw), m_iv):
                    out.append(Finding("seeds", VERIFIED, rel, line,
                                       f"the mean of the {n} listed values is {got:.6g}, "
                                       f"as stated"))
                else:
                    out.append(Finding("seeds", FLAGGED, rel, line,
                                       f"the {n} listed values average {got:.6g}, "
                                       f"but the text says {m_raw}",
                                       "checked at the precision the values are printed to"))
                # 2. the stated spread must follow from them too
                cands = [s for s in (_std(xs, 1), _std(xs, 0)) if s is not None]
                widen = h * math.sqrt(n / max(n - 1, 1))
                if cands and not any(_overlaps(_iv(s_raw), (c - widen, c + widen)) for c in cands):
                    shown = " or ".join(f"{c:.4g}" for c in cands)
                    out.append(Finding("seeds", FLAGGED, rel, line,
                                       f"the {n} listed values have a spread of {shown}, "
                                       f"but the text says {s_raw}",
                                       "both the sample and population conventions were tried"))

    # 4. standard error and standard deviation must agree for n runs
    sem, sd = SEM_STD.search(clean), STD_WORD.search(clean)
    if sem and sd and sn:
        n = int(sn.group("n"))
        if n > 1:
            sd_lo, sd_hi = _iv(sd.group("sd"))
            want = (sd_lo / math.sqrt(n), sd_hi / math.sqrt(n))
            if not _overlaps(_iv(sem.group("sem")), want):
                out.append(Finding("seeds", FLAGGED, rel, line,
                                   f"a standard deviation of {sd.group('sd')} over {n} runs "
                                   f"gives a standard error of {_f(sd.group('sd')) / math.sqrt(n):.4g}, "
                                   f"but the text says {sem.group('sem')}"))

    # 5. reported. not called an error.
    if SIGNIF.search(clean) and not TEST_NAMED.search(clean):
        pairs = MEAN_STD.findall(clean)
        if len(pairs) >= 2:
            (m1, s1), (m2, s2) = pairs[0], pairs[1]
            a = (_f(m1) - _f(s1), _f(m1) + _f(s1))
            b = (_f(m2) - _f(s2), _f(m2) + _f(s2))
            if _overlaps(a, b):
                out.append(Finding("seeds", UNVERIFIABLE, rel, line,
                                   f"{m1}\u00b1{s1} and {m2}\u00b1{s2} are called significant, "
                                   f"but the error bars overlap",
                                   "no test is named here, so these numbers alone "
                                   "do not establish it either way"))
    return out
