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

# See benchmarks.N: longest alternative first, and a trailing guard so that
# "2023" cannot be read as "202".
N = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\.?\d)"
SN = r"[-\u2212+]?(?:" + N + r")"
PM = r"(?:\u00b1|\+/-|\+/\u2212|\+-)"
RUN = r"seeds?|runs?|trials?|replicates?|restarts?|folds?"

MEAN_STD = re.compile(r"(?P<m>" + SN + r")\s*(?:%|pp)?\s*" + PM +
                      r"\s*(?P<s>" + N + r")\s*(?:%|pp)?")
SEED_N = re.compile(r"(?:over|across|from|using|with|of)\s+(?P<n>\d{1,3})\s+(?:" + RUN + r")", re.I)
# The list has to be what the run word introduces. A window of 30 characters
# before the colon let "with 5 seeds and report per-epoch losses:" through,
# and then read six epoch losses as five seed results.
VALUES = re.compile(r"(?:per[-\s](?:" + RUN + r")(?:\s+\w+){0,2}"
                    r"|(?:over|across|for|from|using|with|of|on|in)\s+(?:all\s+)?"
                    r"(?:\d{1,3}\s+)?(?:" + RUN + r")"
                    r"|(?:" + RUN + r"))"
                    r"\s*:\s*\(?\s*(?P<vals>" + SN +
                    r"(?:\s*,\s*" + SN + r"){2,})", re.I)
# The captured number has to be predicated on the phrase. Taking the first
# number within 40 characters picked up "the standard error in Table 2 is
# 0.40" as a standard error of 2.
SEM_STD = re.compile(r"standard\s+error(?P<gap>[^.]{0,40}?)\s*\b(?:is|of|was|are|were|=)\s*"
                     r"(?P<sem>" + N + r")", re.I)
STD_WORD = re.compile(r"standard\s+deviation(?P<gap>[^.]{0,40}?)\s*\b(?:is|of|was|are|were|=)\s*"
                      r"(?P<sd>" + N + r")", re.I)
CROSS_REF = re.compile(r"\b(?:table|figure|fig|section|sec|appendix|eq|equation)\b", re.I)
SIGNIF = re.compile(r"\bsignificant(?:ly)?\b", re.I)
TEST_NAMED = re.compile(r"\b(?:t-?test|wilcoxon|mann-?whitney|bootstrap|permutation|"
                        r"anova|bonferroni|p\s*[<=>]|p-?value)\b", re.I)

# What the "\u00b1" is. Papers write all three and rarely in the same way, so
# the convention is read from the text when it is stated and every
# convention is accepted when it is not.
SEM_CUE = re.compile(r"\bs\.\s?e\.\s?m\.?|\bsem\b|standard\s+error|std\.?\s*err", re.I)
CI_CUE = re.compile(r"\bCI\b|confidence\s+interval|\bconfidence\b", re.I)
STD_CUE = re.compile(r"standard\s+deviation|\bs\.?d\.?\b|\bstd\b|\u03c3", re.I)
# Two-sided 95% critical values of Student's t by degrees of freedom.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
       8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
       14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
       20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
       26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}

# Words allowed between a list of values and the "x \u00b1 y" that summarises
# it. A whitelist rather than a blocklist: an unrecognised word means the
# pair is attached to something else -- "with a target of 95.0 \u00b1 1.0",
# "median 2.0 \u00b1 4.4" -- and the listed values do not describe it.
CONNECTOR = {"mean", "average", "avg", "\u00b5", "\u03bc", "mu", "overall", "giving",
             "yielding", "yields", "gives", "for", "so", "with", "and", "at", "of",
             "a", "an", "the", "is", "was", "are", "were", "to", "in", "on",
             "resulting", "reaching", "or", "i.e", "e.g", "namely", "about",
             "roughly", "approximately", "around", "we", "report", "i", "it"}
WORDS = re.compile(r"[A-Za-z\u00b5\u03bc][A-Za-z\u00b5\u03bc.]*")
# A physical tolerance is not an error bar.
UNIT = re.compile(r"\s*(?:degrees?|\u00b0[CFK]?|celsius|fahrenheit|kelvin|mm|cm|km|kg|mg|"
                  r"ms|ns|\u00b5s|seconds?|secs?|minutes?|hours?|days?|volts?|watts?|amps?|"
                  r"[kmgt]?hz|[kmgt]b|bytes?|px|inch(?:es)?|feet|lbs?|psi|rpm|nm|\u00b5m|"
                  r"met(?:re|er)s?|lit(?:re|er)s?|joules?|newtons?)\b", re.I)


def _f(s):
    return float(str(s).replace(",", "").replace("\u2212", "-"))


def _fmt(v):
    """A number for a message, never in scientific notation."""
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.4g}"
    if "e" in s or "E" in s:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def _plural(n, word, verb=("is", "are")):
    return f"{n} {word}{'' if n == 1 else 's'} {verb[0] if n == 1 else verb[1]}"


def _attached(gap):
    """Does "x \u00b1 y" summarise the list that precedes it, or something else?"""
    return all(w.lower().rstrip(".") in CONNECTOR for w in WORDS.findall(gap))


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
    """The numbers in a comma-separated list, tokenised rather than split.

    Splitting on "," turns "1,000.0, 2,000.0" into four values, because the
    same comma is both the separator and the thousands mark. Matching the
    number pattern instead lets the pattern decide which is which.
    """
    return re.findall(SN, raw)


def _spread_readings(xs, window):
    """-> ([(name, value, rounding_scale)], disclosure) for what "\u00b1 s" could be.

    Reading every "\u00b1" as a standard deviation flags "mean \u00b1 s.e.m." and
    "\u00b1 95% CI" as arithmetic errors, and both are everywhere in ML papers.
    When the text names the convention, that one is checked. When it does
    not, all three are accepted, which can only ever suppress a report.
    """
    n = len(xs)
    stds = [s for s in (_std(xs, 1), _std(xs, 0)) if s is not None]
    if not stds:
        return [], ""
    root = math.sqrt(n)
    sems = [("standard error", s / root, 1 / root) for s in stds]
    t = T95.get(max(n - 1, 1), 1.96)
    cis = [("95% confidence half-width", s / root * k, k / root)
           for s in stds for k in (t, 1.96)]
    devs = [("standard deviation", s, 1.0) for s in stds]
    if SEM_CUE.search(window):
        return sems, "read as a standard error, which is what the text calls it"
    if CI_CUE.search(window):
        return cis, "read as a 95% confidence half-width, which is what the text calls it"
    if STD_CUE.search(window):
        return devs, "read as a standard deviation, which is what the text calls it"
    return devs + sems + cis, ("the text does not say which of these the \u00b1 is, "
                               "so all three were tried")


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
    sn = SEED_N.search(clean)

    if vm:
        raws = _values(vm.group("vals"))
        # The "\u00b1" that summarises the list is the one that directly follows
        # it with nothing but a connector in between.
        ms = MEAN_STD.search(clean, vm.end())
        if ms and not _attached(clean[vm.end():ms.start()]):
            ms = None
        if _are_identifiers(raws):
            # A decimal-free list is read as seed numbers rather than
            # results, which is a deliberate trade. Saying nothing at all
            # about a sentence that also states a mean would let the skip
            # read as approval, so the skip is disclosed instead.
            if ms:
                out.append(Finding("seeds", UNVERIFIABLE, rel, line,
                                   f"the {len(raws)} listed values have no decimal point, so "
                                   f"they were read as seed numbers and the stated "
                                   f"{ms.group('m')} \u00b1 {ms.group('s')} was not checked "
                                   f"against them",
                                   "write the results with a decimal point to have them "
                                   "checked"))
        else:
            xs = [_f(r) for r in raws]
            h = max(_half(r) for r in raws)
            n = len(xs)

            # 3. the seed count must be the number of values listed
            if sn and int(sn.group("n")) != n:
                out.append(Finding("seeds", FLAGGED, rel, line,
                                   f"{_plural(int(sn.group('n')), 'run')} claimed but "
                                   f"{_plural(n, 'value')} listed",
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
                window = clean[max(0, ms.start() - 60):ms.end() + 60]
                cands, why = _spread_readings(xs, window)
                widen = h * math.sqrt(n / max(n - 1, 1))
                if cands and not any(_overlaps(_iv(s_raw), (c - widen * sc, c + widen * sc))
                                     for _name, c, sc in cands):
                    groups = {}
                    for name, c, _sc in cands:
                        lo, hi = groups.get(name, (c, c))
                        groups[name] = (min(lo, c), max(hi, c))
                    shown = " or ".join(
                        (f"{_fmt(lo)} as a {name}" if lo == hi
                         else f"{_fmt(lo)} to {_fmt(hi)} as a {name}")
                        for name, (lo, hi) in groups.items())
                    out.append(Finding("seeds", FLAGGED, rel, line,
                                       f"the {n} listed values have a spread of {shown}, "
                                       f"but the text says {s_raw}",
                                       why + "; both the sample and population "
                                       "conventions were tried"))

    # 4. standard error and standard deviation must agree for n runs
    sem, sd = SEM_STD.search(clean), STD_WORD.search(clean)
    if sem and sd and CROSS_REF.search(sem.group("gap") + sd.group("gap")):
        sem = sd = None     # a cross-reference intervenes; the number is a label
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
        # A "\u00b1" carrying a physical unit is a tolerance, not an error bar:
        # "held at 20 \u00b1 2 degrees" says nothing about sampling.
        pairs = [m for m in MEAN_STD.finditer(clean) if not UNIT.match(clean, m.end())]
        if len(pairs) >= 2:
            (m1, s1), (m2, s2) = [p.group("m", "s") for p in pairs[:2]]
            a = (_f(m1) - _f(s1), _f(m1) + _f(s1))
            b = (_f(m2) - _f(s2), _f(m2) + _f(s2))
            if _overlaps(a, b):
                out.append(Finding("seeds", UNVERIFIABLE, rel, line,
                                   f"{m1}\u00b1{s1} and {m2}\u00b1{s2} are called significant, "
                                   f"but the two stated intervals overlap",
                                   "no test is named here, so these numbers alone "
                                   "do not establish it either way"))
    return out
