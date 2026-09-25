"""Check the arithmetic in benchmark comparisons against itself.

"We reach 91.2 against the baseline's 88.0, a gain of 4.2 points" is wrong
on its own terms. No table, no data file and no judgement is needed to say
so, which makes this one of the few checks that can be both automatic and
certain. It is the same idea as GRIM in psychology: test whether the
reported numbers can coexist.

Everything here is interval arithmetic on the printed precision. 91.2 means
the true value is somewhere in [91.15, 91.25), so the difference from 88.0
is somewhere in (3.10, 3.30), and a stated gain of 3.2 is consistent while a
stated gain of 4.2 is not. Rounding therefore cannot produce a false
positive, which matters more here than catching every case: a tool that
cries wolf about rounding gets switched off.

Only sentences that state all the numbers are checked. If the baseline is in
a table somewhere and the sentence says "a 4.2 point gain", nothing is
guessed and nothing is reported.
"""
from __future__ import annotations
import re

from . import claims
from .report import Finding, VERIFIED, FLAGGED

# A number, whole. The thousands-separated form is tried first because the
# plain form would otherwise match "1" of "1,000", and the trailing guard
# stops the alternation from settling for a prefix: without it "2023" is
# matched as "202" and every four-digit token in the corpus is corrupted.
N = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\.?\d)"
_PCT = r"\s*(?:%|percent|pp\b|percentage points?)?"
# "91.2 vs 88.0", "91.2 compared to 88.0", "91.2 against 88.0"
AGAINST = re.compile(r"(?P<a>" + N + r")" + _PCT +
                     r"\s*(?:vs\.?|versus|compared\s+(?:to|with)|against)\s*"
                     r"(?:the\s+)?(?:baseline'?s?\s+)?(?P<b>" + N + r")" + _PCT, re.I)
# "from 88.0 to 91.2", "improves 88.0 to 91.2"
FROM_TO = re.compile(r"\bfrom\s+(?P<a>" + N + r")" + _PCT +
                     r"\s+to\s+(?P<b>" + N + r")" + _PCT, re.I)
# the stated size of the change
GAIN = re.compile(r"(?:a|an)?\s*(?P<d>" + N + r")\s*(?:%|percentage\s+points?|pp\b|points?\b)"
                  r"\s*(?:absolute\s+|relative\s+)?"
                  r"(?:gain|improvement|increase|reduction|decrease|drop|jump|margin|difference|better|higher|lower)"
                  r"|(?:gain|improvement|increase|reduction|decrease|drop|jump|margin|difference)\s+of\s+"
                  r"(?P<d2>" + N + r")\s*(?:%|percentage\s+points?|pp\b|points?\b)"
                  # already scoped to one sentence, so a decimal point may be spanned
                  r"|\b(?:beats?|outperform(?:s|ing)?|exceeds?|improv(?:es|ing)|reduc(?:es|ing)|lead(?:s)?)\b.{0,60}?"
                  r"\bby\s+(?P<d3>" + N + r")\s*(?:%|percentage\s+points?|pp\b|points?\b)", re.I)
# Applied to the stated change phrase alone, never to the whole sentence:
# "Relative to the baseline, we reach 91.2 versus 88.0, a gain of 3.2 points"
# is an absolute claim that happens to open with the word.
RELATIVE = re.compile(r"relative", re.I)
PT_UNIT = re.compile(r"\bpoints?\b|\bpp\b|percentage\s+points?", re.I)
# Between the pair and the stated change, any of these means the change is
# being attributed to something other than the pair.
CLAUSE_BREAK = re.compile(r";|\b(?:while|whereas|although|though|but|however)\b", re.I)
# "ranged from 88.0 to 91.2" is the spread of a set of results, not a change
# from one to the other, so there is no difference to check.
RANGE_CUE = re.compile(r"\b(?:rang(?:e|es|ed|ing)|var(?:y|ies|ied|ying)|fluctuat\w*|"
                       r"between|anywhere|spann?(?:s|ed|ing)?)\s*$", re.I)
# The first operand is the tail of a list of results, so which of them the
# stated change refers to is a guess.
LIST_BEFORE = re.compile(r"(?:" + N + r")\s*(?:,|,?\s+(?:and|or))\s*$", re.I)
# A learning-rate or similar schedule. The arithmetic may well be right, but
# a verdict on a hyperparameter is noise, not an audit finding.
SCHEDULE = re.compile(r"\b(?:learning\s+rate|lr|weight\s+decay|dropout|momentum|"
                      r"temperature|warm-?up|betas?|epsilon|batch\s+size|"
                      r"hyper-?parameters?)\b", re.I)
# "0.35 vs 0.49, about -14pp": a bare delta with no gain word. Anchored
# directly to the end of the comparison pair, because anything in between
# usually means the number is a second measurement rather than a gap.
# "89.0% at 3.93pp versus 88.3% at 5.48pp" is two pairs of coverage and
# width, and the "at" is what tells us 5.48 is not the difference.
BARE = re.compile(r"[\s,;(\u2013\u2014-]*(?:about|roughly|approximately|around|some)?\s*"
                  r"(?P<d4>[-\u2212+]?(?:" + N + r"))\s*(?:pp\b|percentage\s+points?)", re.I)


def _f(s):
    return float(str(s).replace(",", "").replace("\u2212", "-"))


def _fmt(v):
    """A number for a message. Never in scientific notation.

    "%.4g" turns a difference of 10^21 into "1e+21", which reads as a
    different kind of quantity than the operands it was computed from.
    """
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.4g}"
    if "e" in s or "E" in s:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def _half(raw):
    """Half a unit in the last printed place: the rounding half-width."""
    raw = str(raw)
    dec = len(raw.split(".")[1]) if "." in raw else 0
    return 0.5 * 10 ** (-dec)


def _iv(raw):
    v, h = _f(raw), _half(raw)
    return v - h, v + h


def _abs_iv(lo, hi):
    if lo >= 0:
        return lo, hi
    if hi <= 0:
        return -hi, -lo
    return 0.0, max(-lo, hi)


def _overlaps(x, y, eps=1e-9):
    return x[0] - eps <= y[1] and y[0] - eps <= x[1]


def _rel_iv(a_raw, b_raw):
    """Interval for 100*|b-a|/a over the rounding boxes of a and b."""
    alo, ahi = _iv(a_raw)
    blo, bhi = _iv(b_raw)
    if alo <= 0 <= ahi:
        return None  # division by something that could be zero
    vals = [100.0 * abs(b - a) / abs(a)
            for a in (alo, ahi) for b in (blo, bhi)]
    return min(vals), max(vals)


def _joined(gap):
    """Is the text between the pair and the stated change empty of reasons
    to think the two belong to different claims?"""
    return not (CLAUSE_BREAK.search(gap) or AGAINST.search(gap) or FROM_TO.search(gap))


def _stated(sentence, after=0):
    """The stated size of the change, if it belongs to the pair.

    GAIN used to search the whole sentence with no positional relationship
    to the matched pair, so "Table 3 shows a 5 point improvement; here we
    report 91.2 versus 88.0" bound a number from one clause to a pair in
    another. The number itself now has to come after the pair and in the
    same clause, which is the rule BARE already followed. The match may
    still begin before the pair, because "beats the baseline, 91.2 vs 88.0,
    by 7.0 points" puts the verb first and the number last.
    """
    for m in GAIN.finditer(sentence):
        for g in ("d", "d2", "d3"):
            if m.group(g) is None:
                continue
            i = m.start(g)
            if i >= after and _joined(sentence[after:i]):
                return m.group(g), m.group(0)
    m = BARE.match(sentence, after)
    if m:
        return m.group("d4"), m.group(0)
    return None


def check(tfiles):
    out = []
    for rel, _abs, text in tfiles:
        for line, sent in claims.sentences(text, rel):
            out += _sentence(rel, line, claims._URLISH.sub(" ", sent))
    return out


def _sentence(rel, line, clean):
    against = list(AGAINST.finditer(clean))
    fromto = list(FROM_TO.finditer(clean))
    # Exactly one comparison frame, or there is nothing here to be sure
    # about. None means the other value is not in this sentence. More than
    # one means the stated change could belong to either of them, and
    # picking whichever sits nearest the verb is a guess.
    if len(against) + len(fromto) != 1:
        return []
    pair = (against or fromto)[0]
    is_against = bool(against)
    if SCHEDULE.search(clean):
        return []
    before = clean[:pair.start()]
    if not is_against and RANGE_CUE.search(before):
        return []
    if LIST_BEFORE.search(before):
        return []
    st = _stated(clean, pair.end())
    if not st:
        return []
    d_raw, phrase = st
    a_raw, b_raw = pair.group("a"), pair.group("b")
    if d_raw in (a_raw, b_raw) and clean.count(d_raw) < 2:
        return []  # the "difference" is one of the two values being read twice
    # Only the magnitude is checked: "a gain of 3.2" and "-3.2pp" describe
    # the same arithmetic from opposite directions.
    d_iv = _abs_iv(*_iv(d_raw))

    alo, ahi = _iv(a_raw)
    blo, bhi = _iv(b_raw)
    abs_iv = _abs_iv(alo - bhi, ahi - blo)
    abs_shown = _fmt(abs(_f(a_raw) - _f(b_raw)))
    # Tables often report proportions where the prose reports percentage
    # points, so 0.35 vs 0.49 is a 14pp gap. Accepting either reading only
    # ever suppresses a report, so it cannot create a false positive.
    if max(abs(_f(a_raw)), abs(_f(b_raw))) <= 1.5:
        scaled = (abs_iv[0] * 100.0, abs_iv[1] * 100.0)
        if _overlaps(d_iv, scaled):
            abs_iv = scaled
            abs_shown = _fmt(abs(_f(a_raw) - _f(b_raw)) * 100)

    # "A versus B" names the new value first and the baseline second; "from
    # A to B" the other way round. Dividing by the wrong one inverted the
    # check on every vs/compared-to sentence. Both denominators are offered,
    # because accepting either can only suppress a report.
    base_raw, other_raw = (b_raw, a_raw) if is_against else (a_raw, b_raw)
    rel_readings = []
    # An operand whose rounding box straddles zero makes a ratio either
    # undefined or so wide that it would accept any stated figure at all.
    if not any(_iv(r)[0] <= 0 <= _iv(r)[1] for r in (a_raw, b_raw)):
        for den, num in ((base_raw, other_raw), (other_raw, base_raw)):
            iv = _rel_iv(den, num)
            if iv is not None:
                rel_readings.append(("relative change", iv,
                                     _fmt(100 * abs(_f(num) - _f(den)) / abs(_f(den))) + "%"))

    absolute = ("difference", abs_iv, abs_shown)
    if PT_UNIT.search(phrase):
        readings = [absolute]          # the sentence names its unit; believe it
    elif RELATIVE.search(phrase):
        readings = rel_readings
    elif "%" in phrase:
        # "91.2 versus 88.0, a 3.6% improvement" is read both ways in real
        # papers, and one optional adjective must not decide the verdict.
        readings = [absolute] + rel_readings
    else:
        readings = [absolute]
    if not readings:
        return []

    hit = next((r for r in readings if _overlaps(d_iv, r[1])), None)
    if hit:
        return [Finding("benchmark", VERIFIED, rel, line,
                        f"the stated {hit[0]} of {d_raw} matches {a_raw} and {b_raw}")]
    grouped = []
    for k, _i, s in readings:
        if grouped and grouped[-1][0] == k:
            grouped[-1][1].append(s)
        else:
            grouped.append((k, [s]))
    kinds = " or a ".join(f"{k} of {' or '.join(dict.fromkeys(ss))}" for k, ss in grouped)
    return [Finding("benchmark", FLAGGED, rel, line,
                    f"{a_raw} and {b_raw} give a {kinds}, but the text says {d_raw}",
                    "checked at the precision the numbers are printed to, "
                    "so this is not a rounding difference")]
