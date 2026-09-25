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
RELATIVE = re.compile(r"relative|relatively", re.I)
# "0.35 vs 0.49, about -14pp": a bare delta with no gain word. Anchored
# directly to the end of the comparison pair, because anything in between
# usually means the number is a second measurement rather than a gap.
# "89.0% at 3.93pp versus 88.3% at 5.48pp" is two pairs of coverage and
# width, and the "at" is what tells us 5.48 is not the difference.
BARE = re.compile(r"[\s,;(\u2013\u2014-]*(?:about|roughly|approximately|around|some)?\s*"
                  r"(?P<d4>[-\u2212+]?(?:" + N + r"))\s*(?:pp\b|percentage\s+points?)", re.I)


def _f(s):
    return float(str(s).replace(",", "").replace("\u2212", "-"))


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


def _stated(sentence, after=0):
    m = GAIN.search(sentence)
    if m:
        return (m.group("d") or m.group("d2") or m.group("d3")), m.group(0)
    m = BARE.match(sentence, after)
    if m:
        return m.group("d4"), m.group(0)
    return None


def check(tfiles):
    out = []
    for rel, _abs, text in tfiles:
        for line, sent in claims.sentences(text, rel):
            clean = claims._URLISH.sub(" ", sent)
            pair = AGAINST.search(clean) or FROM_TO.search(clean)
            if not pair:
                continue  # the other value is not in this sentence; guess nothing
            st = _stated(clean, pair.end())
            if not st:
                continue
            d_raw, phrase = st
            a_raw, b_raw = pair.group("a"), pair.group("b")
            if d_raw in (a_raw, b_raw) and clean.count(d_raw) < 2:
                continue  # the "difference" is one of the two values being read twice
            # Only the magnitude is checked: "a gain of 3.2" and "-3.2pp"
            # describe the same arithmetic from opposite directions.
            d_iv = _abs_iv(*_iv(d_raw))
            relative = bool(RELATIVE.search(clean)) or (
                "%" in phrase and "point" not in phrase.lower() and "pp" not in phrase.lower()
                and FROM_TO.search(clean) is not None)

            if relative:
                got = _rel_iv(a_raw, b_raw)
                if got is None:
                    continue
                kind, shown = "relative change", f"{100 * abs(_f(b_raw) - _f(a_raw)) / abs(_f(a_raw)):.4g}%"
            else:
                alo, ahi = _iv(a_raw)
                blo, bhi = _iv(b_raw)
                got = _abs_iv(alo - bhi, ahi - blo)
                kind, shown = "difference", f"{abs(_f(a_raw) - _f(b_raw)):.4g}"
                # Tables often report proportions where the prose reports
                # percentage points, so 0.35 vs 0.49 is a 14pp gap. Accepting
                # either reading only ever suppresses a report, so it cannot
                # create a false positive.
                if max(abs(_f(a_raw)), abs(_f(b_raw))) <= 1.5:
                    scaled = (got[0] * 100.0, got[1] * 100.0)
                    if _overlaps(d_iv, scaled):
                        got = scaled
                        shown = f"{abs(_f(a_raw) - _f(b_raw)) * 100:.4g}"

            msg = f"{a_raw} and {b_raw} give a {kind} of {shown}, but the text says {d_raw}"
            if _overlaps(d_iv, got):
                out.append(Finding("benchmark", VERIFIED, rel, line,
                                   f"the stated {kind} of {d_raw} matches {a_raw} and {b_raw}"))
            else:
                out.append(Finding("benchmark", FLAGGED, rel, line, msg,
                                   "checked at the precision the numbers are printed to, "
                                   "so this is not a rounding difference"))
    return out
