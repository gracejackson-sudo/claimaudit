"""Flag the same claim appearing with different numbers in different files."""
from __future__ import annotations
from collections import defaultdict
from itertools import islice
from .report import Finding, FLAGGED


def _close(a, b):
    dec = min(a.decimals, b.decimals)
    return abs(abs(a.value) - abs(b.value)) <= 0.5 * 10 ** (-dec) * 1.0001 + 1e-9


def _pair(a, b):
    """Return list of (na, nb) mismatches if a and b make the same-shaped claim."""
    ka = sorted(n.kind for n in a.nums)
    kb = sorted(n.kind for n in b.nums)
    if ka != kb:
        return []
    bad = []
    used = set()
    for n in a.nums:
        hit = next((j for j, m in enumerate(b.nums) if j not in used and m.kind == n.kind
                    and _close(n, m) and (n.den is None or (m.den is not None and abs(n.den - m.den) < 0.5))), None)
        if hit is not None:
            used.add(hit)
        else:
            bad.append(n)
    if not bad:
        return []
    rest = [m for j, m in enumerate(b.nums) if j not in used]
    return list(zip(bad, rest))


def check(claims):
    inv = defaultdict(list)
    for i, c in enumerate(claims):
        if len(c.tokens) >= 3:
            for t in c.tokens:
                inv[t].append(i)
    seen, out = set(), []
    for i, a in enumerate(claims):
        if len(a.tokens) < 3:
            continue
        counts = defaultdict(int)
        for t in a.tokens:
            lst = inv[t]
            if len(lst) > 60:
                continue
            for j in lst:
                if j > i and claims[j].file != a.file:
                    counts[j] += 1
        for j, k in counts.items():
            b = claims[j]
            if k < 3 or (i, j) in seen:
                continue
            jac = len(a.tokens & b.tokens) / len(a.tokens | b.tokens)
            if jac < 0.5:
                continue
            seen.add((i, j))
            mism = _pair(a, b)
            if mism:
                na, nb = mism[0]
                out.append(Finding(
                    "consistency", FLAGGED, a.file, a.line,
                    f"says {na.raw}, but {b.file}:{b.line} says {nb.raw} in a similar sentence",
                    f"A: {a.sentence[:120]} || B: {b.sentence[:120]}"))
    return out
