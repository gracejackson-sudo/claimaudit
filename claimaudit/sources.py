"""Check numeric claims against CSV/JSON files found in the same directory.

Deliberately conservative. A claim is VERIFIED only when a number in the data
(a cell or a simple aggregate: count, mean, median, min, max, sum, std, share
true, distinct count) equals the claimed number at the claimed precision AND the
sentence shares at least one word with that value's column / key / file name.
A number that matches only by coincidence is reported UNVERIFIABLE, not VERIFIED.
A claim is FLAGGED only when the sentence itself points at a data file or column
and none of that file's values match.
"""
from __future__ import annotations
import bisect, csv, json, math, os, re, statistics
from dataclasses import dataclass

from .claims import tokens_of
from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

MAX_ROWS = 300_000
CELL_LIMIT = 3000


@dataclass
class Src:
    value: float
    file: str
    where: str
    stem: frozenset = frozenset()   # words from the file name
    col: frozenset = frozenset()    # words from the column / key path
    label: frozenset = frozenset()  # words from the row label (cells only)
    agg: frozenset = frozenset()    # generic aggregate words (mean, count, ...) -- never enough alone


def _split_name(s):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return tokens_of(re.sub(r"[_\-.\[\]/]", " ", s))


def _num(x):
    if x is None:
        return None
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return None if isinstance(x, float) and not math.isfinite(x) else float(x)
    s = str(x).strip().replace(",", "")
    if s.lower() in ("true", "false"):
        return 1.0 if s.lower() == "true" else 0.0
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


class Index:
    def __init__(self):
        self.items: list[Src] = []
        self.files: dict[str, dict] = {}      # rel -> {"name","stem","headers":{header:tokens}}
        self._keys = None
        self.problems: list[tuple] = []       # (rel, why this file was not indexed)

    def add(self, value, file, where, stem=(), col=(), label=(), agg=()):
        self.items.append(Src(value, file, where, frozenset(stem), frozenset(col),
                              frozenset(label), frozenset(agg)))

    def _aggs(self, vals, file, header, stem, col):
        n = len(vals)
        if n == 0:
            return
        def put(v, what, extra=()):
            self.add(v, file, f"{header} {what}", stem, col, (), extra)
        put(n, "count", ["count", "number", "total"])
        put(sum(vals) / n, "mean", ["mean", "average", "avg"])
        put(statistics.median(vals), "median", ["median"])
        put(min(vals), "min", ["min", "minimum", "lowest", "worst"])
        put(max(vals), "max", ["max", "maximum", "highest", "best", "worst"])
        put(sum(vals), "sum", ["sum", "total"])
        if n > 1:
            put(statistics.stdev(vals), "std", ["std", "sd", "deviation"])
        if all(v in (0.0, 1.0) for v in vals):
            put(100 * sum(vals) / n, "share", ["share", "rate", "percent", "coverage"])
            put(sum(vals), "count true", ["count"])
        elif all(0.0 <= v <= 1.0 for v in vals):
            for what, v in (("mean", sum(vals) / n), ("median", statistics.median(vals)),
                            ("min", min(vals)), ("max", max(vals))):
                put(100 * v, f"{what} x100", [what, "percent"])

    def add_csv(self, rel, path):
        try:
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
                rd = csv.DictReader(fh)
                cols = rd.fieldnames or []
                rows, truncated = [], False
                for i, r in enumerate(rd):
                    if i >= MAX_ROWS:
                        truncated = True
                        break
                    rows.append(r)
        except (csv.Error, OSError):
            return
        if truncated:
            # Every aggregate, and the row count itself, would be computed over
            # a partial read. Indexing it would let the tool confirm a number
            # that is wrong, which is worse than declining to check at all.
            self.problems.append((rel, f"has more than {MAX_ROWS:,} rows, so it was not indexed; "
                                       f"no claim was checked against it"))
            return
        if not cols or not rows:
            return
        stem = _split_name(os.path.splitext(os.path.basename(rel))[0])
        info = {"name": os.path.basename(rel).lower(), "stem": stem, "headers": {}}
        self.files[rel] = info
        self.add(len(rows), rel, "row count", stem, (), (), {"row", "rows", "count", "total"})
        labelcol = next((c for c in cols if any(_num(r.get(c)) is None and r.get(c)
                                                for r in rows[:20])), None)
        for c in cols:
            if c is None:
                continue
            ctoks = _split_name(c)
            info["headers"][c] = ctoks
            raw = [r.get(c) for r in rows]
            nz = [x for x in raw if x not in (None, "")]
            nums = [_num(x) for x in nz]
            if nz and sum(v is not None for v in nums) >= 0.9 * len(nz):
                vals = [v for v in nums if v is not None]
                self._aggs(vals, rel, c, stem, ctoks)
                if len(rows) <= CELL_LIMIT:
                    for r, x in zip(rows, raw):
                        v = _num(x)
                        if v is not None:
                            lab = _split_name(str(r.get(labelcol, ""))) if labelcol else set()
                            self.add(v, rel, f"{c} [{r.get(labelcol, '')}]", stem, ctoks, lab)
            elif nz:
                d = set(nz)
                self.add(len(d), rel, f"{c} distinct", stem, ctoks, (), {"distinct", "unique", "number"})
                if len(d) <= 30:
                    from collections import Counter
                    for k, cnt in Counter(nz).items():
                        self.add(cnt, rel, f"{c}={k} count", stem, ctoks, _split_name(str(k)), {"count"})

    def add_json(self, rel, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            return
        stem = _split_name(os.path.splitext(os.path.basename(rel))[0])
        self.files[rel] = {"name": os.path.basename(rel).lower(), "stem": stem, "headers": {}}
        budget = [500_000]

        def walk(o, path_, toks):
            if budget[0] <= 0:
                return
            budget[0] -= 1
            if isinstance(o, dict):
                for k, v in o.items():
                    kt = _split_name(str(k))
                    self.files[rel]["headers"].setdefault(str(k), kt)
                    walk(v, f"{path_}.{k}" if path_ else str(k), toks | kt)
            elif isinstance(o, list):
                nums = [_num(x) for x in o if not isinstance(x, (dict, list))]
                if o and len(nums) == len(o) and all(v is not None for v in nums) and len(o) > 1:
                    self._aggs(nums, rel, path_, stem, toks)
                else:
                    self.add(len(o), rel, f"{path_} length", stem, toks, (), {"count", "number"})
                for i, x in enumerate(o[:5000]):
                    walk(x, f"{path_}[{i}]", toks)
            else:
                v = _num(o)
                if v is not None and not isinstance(o, str):
                    self.add(v, rel, path_, stem, toks)
        walk(data, "", set())

    def finalize(self):
        self.items.sort(key=lambda s: s.value)
        self._keys = [s.value for s in self.items]

    def find(self, target, tol):
        lo = bisect.bisect_left(self._keys, target - tol)
        hi = bisect.bisect_right(self._keys, target + tol)
        return self.items[lo:hi]


# ---------------------------------------------------------------- verification
def _tol(dec, v):
    return 0.5 * 10 ** (-dec) * 1.0001 + 1e-9 * max(1.0, abs(v))


def _candidates(idx, n):
    tol = _tol(n.decimals, n.value)
    if n.kind == "ratio":
        return idx.find(n.value, 0.5), idx.find(n.den, 0.5)
    out = list(idx.find(n.value, tol))
    if n.kind == "pp" or n.value < 0:
        out += idx.find(-n.value, tol)
    if n.kind == "pct":
        out += idx.find(n.value / 100.0, _tol(n.decimals + 2, n.value / 100.0))
    return out, None


def _sig(raw):
    """Significant digits as printed: '90.0' has 3, '100' has 1, '0.529' has 3."""
    d = re.sub(r"\D", "", raw.split("e")[0]).lstrip("0")
    return len(d) if "." in raw else len(d.rstrip("0")) or 1


def _score(claim_tokens, src):
    """2 per column/key word, 1 per file-name word, at most 1 for row-label words.
    Generic aggregate words (mean, count, ...) score 0."""
    bonus = 2 if src.where == "row count" and claim_tokens & {"row", "rows"} else 0
    return (2 * len(claim_tokens & src.col) + len(claim_tokens & src.stem)
            + min(1, len(claim_tokens & src.label)) + bonus)


def _need(raw):
    """Less specific numbers need more evidence: short numbers match by chance."""
    k = _sig(raw)
    return 2 if k >= 4 else 3 if k == 3 else 4


def _ok(claim_tokens, src, raw):
    return _score(claim_tokens, src) >= _need(raw)


def _referenced(claim, idx):
    """Files the sentence itself points at: literal file name, or >=3 stem words,
    or a literal column/key name of at least 4 characters."""
    refs = []
    low = claim.sentence.lower()
    for rel, info in idx.files.items():
        stem_lit = os.path.splitext(info["name"])[0]
        if info["name"] in low or rel.lower() in low or (len(stem_lit) >= 5 and re.search(
                r"(?<![\w])" + re.escape(stem_lit) + r"(?![\w])", low) and ("_" in stem_lit or "-" in stem_lit)):
            refs.append(rel); continue
        if len(info["stem"]) >= 3 and info["stem"] <= claim.tokens:
            refs.append(rel); continue
        for h in info["headers"]:
            hl = h.lower()
            if len(hl) >= 4 and ("_" in hl or " " in hl) and hl in low:
                refs.append(rel); break
    return refs


def verify(claims, idx):
    out = []
    for c in claims:
        parts, status_all = [], []
        refs = _referenced(c, idx)
        for n in c.nums:
            cand, cand2 = _candidates(idx, n)
            if n.kind == "ratio":
                a = [s for s in cand if _ok(c.tokens, s, str(int(n.value)))]
                b = [s for s in cand2 if _ok(c.tokens, s, str(int(n.den)))]
                if a and b:
                    parts.append((VERIFIED, n.raw, f"{a[0].file}: {a[0].where}; {b[0].where}"))
                    continue
                cand = list(cand) + list(cand2)
            good = [s for s in cand if _ok(c.tokens, s, n.raw)]
            if good:
                best = max(good, key=lambda s: _score(c.tokens, s))
                parts.append((VERIFIED, n.raw, f"{best.file}: {best.where} = {best.value:g}"))
            elif refs and any(s_.file in refs for s_ in cand):
                hit = next(s_ for s_ in cand if s_.file in refs)
                parts.append((VERIFIED, n.raw, f"{hit.file}: {hit.where} = {hit.value:g} (file named in sentence)"))
            elif refs:
                near = sorted((s for s in idx.items if s.file in refs),
                              key=lambda s: abs(s.value - n.value))[:2]
                ev = "; ".join(f"{s.where}={s.value:g}" for s in near)
                parts.append((FLAGGED, n.raw, f"sentence points at {', '.join(refs[:2])} but no value there "
                                                 f"equals {n.raw}; nearest: {ev}"))
            elif cand:
                parts.append((UNVERIFIABLE, n.raw, f"equals {cand[0].file}: {cand[0].where} but the sentence "
                                                    f"shares no words with it (may be coincidence)"))
            else:
                parts.append((UNVERIFIABLE, n.raw, "no matching value in this directory"))
        sts = [p[0] for p in parts]
        st = (FLAGGED if FLAGGED in sts else VERIFIED if all(s == VERIFIED for s in sts)
              else UNVERIFIABLE)
        msg = c.sentence if len(c.sentence) <= 150 else c.sentence[:147] + "..."
        ev = " | ".join(f"{p[0][0]}:{p[1]} {p[2]}" for p in parts if p[0] != UNVERIFIABLE or st == UNVERIFIABLE)
        out.append(Finding("source", st, c.file, c.line, msg, ev[:400]))
    return out


def build_index(data_files):
    idx = Index()
    for rel, p in data_files:
        (idx.add_csv if rel.lower().endswith(".csv") else idx.add_json)(rel, p)
    idx.finalize()
    return idx
