"""Claim extraction: find sentences that make numeric claims."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

STOP = set("""the and for that with this from are was were has have had not but can
will would could should may might into over under than then also such their there
these those which while when where what who whom its our your they them been being
each per via about above below between within without across after before both more
most less least very much many some any all other another same only just like using
used use uses based given show shows shown see set""".split())

COUNT_NOUNS = set("""rows row items item models model files file samples sample users
user cards card checkpoints checkpoint tests test papers paper runs run cells cell
evaluations evaluation benchmarks benchmark publishers publisher schemes scheme
families family features feature examples example queries query documents document
pages page images image tokens token steps step participants respondents customers
requests errors bugs claims citations references sources datasets dataset classes
class layers layer parameters trials trial folds fold seeds seed""".split())

STRUCT_PREV = re.compile(
    r"(?:section|sec\.?|figure|fig\.?|table|eq\.?|equation|page|pp\.?|day|step|"
    r"chapter|appendix|version|v|no\.?|number|item|line|lines|part|phase|week)\s*$",
    re.I)

ABBR = ["et al.", "e.g.", "i.e.", "vs.", "Fig.", "Sec.", "Eq.", "No.", "cf.",
        "approx.", "etc.", "Dr.", "Mr.", "Ms.", "Inc.", "Ph.D."]


@dataclass
class Num:
    value: float
    raw: str
    kind: str          # pct | pp | x | ratio | plain
    decimals: int
    den: float | None = None


@dataclass
class Claim:
    file: str
    line: int
    sentence: str
    nums: list
    tokens: set = field(default_factory=set)


# ------------------------------------------------------------------ cleaning
def _strip_tex(t):
    t = re.sub(r"(?<!\\)%.*", "", t)                      # comments
    t = re.sub(r"\\(?:cite\w*|ref|eqref|label|input|include|url|href)\*?(\[[^\]]*\])?\{[^{}]*\}", " ", t)
    t = t.replace("\\%", "%").replace("\\pm", "±").replace("\\times", "x")
    t = t.replace("{,}", "").replace("~", " ").replace("\\\\", " ")
    t = re.sub(r"\\[()\[\]]", "", t).replace("$", "")
    for _ in range(4):
        t = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?\{([^{}]*)\}", r"\2", t)
    t = re.sub(r"\\[A-Za-z]+\*?", " ", t)
    t = t.replace("&", " | ").replace("{", "").replace("}", "")
    return t


def _strip_md(t):
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`[^`\n]*`", " ", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_]{1,3}([^*_\n]+)[*_]{1,3}", r"\1", t)
    return t


def _units(text, path):
    """Yield (start_line, text, offs): table rows alone, paragraphs joined.
    offs[k] is the char offset in text where line start_line+k+1 begins."""
    is_tex = path.lower().endswith(".tex")
    text = _strip_tex(text) if is_tex else _strip_md(text)
    lines = text.split("\n")
    buf, offs, start = [], [], 0

    def flush():
        nonlocal buf, offs
        if buf:
            yield start, " ".join(buf), offs
        buf, offs = [], []

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        is_row = s.startswith("|") and s.count("|") >= 2
        if not s or is_row or re.match(r"^(#{1,6}\s|[-=]{3,}$)", s):
            yield from flush()
            if is_row and not re.match(r"^\|?[\s:|-]+\|?$", s):
                yield i, s, []
            elif s.startswith("#"):
                yield i, s.lstrip("#").strip(), []
            continue
        s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
        if not buf:
            start = i
        else:
            offs.append(len(" ".join(buf)) + 1)
        buf.append(s)
    yield from flush()


def sentences(text, path):
    import bisect
    for line, unit, offs in _units(text, path):
        u = unit
        for a in ABBR:
            u = u.replace(a, a.replace(".", "\u2024"))
        pos = 0
        for p in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[\"'])", u):
            at = u.find(p, pos)
            pos = at + len(p) if at >= 0 else pos
            p = p.replace("\u2024", ".").strip()
            if p:
                yield line + bisect.bisect_right(offs, max(at, 0)), p


# ------------------------------------------------------------------ numbers
_URLISH = re.compile(r"https?://\S+|www\.\S+|\b10\.\d{4,9}/\S+|arxiv:\s*\d{4}\.\d{4,5}(?:v\d+)?"
                     r"|\b\d{4}\.\d{4,5}v?\d*\b|\d{4}-\d{2}-\d{2}|\[\d+(?:[,–-]\s*\d+)*\]"
                     r"|\bv\d+(?:\.\d+)+\b|\b\d+(?:\.\d+){2,}\b", re.I)
_NUM = re.compile(
    r"(?<![\w.$/\-])(?P<sign>[-−+±]?)(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<unit>\s?(?:%|pp\b|percentage points?\b|percent\b|×|x\b))?(?![\w])")
_RATIO = re.compile(r"(?<![\w.])(\d+(?:,\d{3})*)\s*(?:of|/)\s*(\d+(?:,\d{3})*)(?![\w/])")


def _f(s):
    return float(s.replace(",", ""))


def _dec(s):
    return len(s.split(".")[1]) if "." in s else 0


def extract_numbers(sentence):
    s = _URLISH.sub(" ", sentence)
    s = re.sub(r"(?<=[\d%])\s*(?:--|–|—)\s*(?=\d)", " to ", s)
    out, taken = [], []
    for m in _RATIO.finditer(s):
        a, b = m.group(1), m.group(2)
        if _f(b) >= 2 and _f(a) <= _f(b) * 1.0001 + 1e-9:
            out.append(Num(_f(a), m.group(0), "ratio", 0, _f(b)))
            taken.append(m.span())
    for m in _NUM.finditer(s):
        if any(a <= m.start() < b for a, b in taken):
            continue
        raw = m.group("num")
        unit = (m.group("unit") or "").strip().lower()
        kind = ("pct" if unit in ("%", "percent") else "pp" if unit.startswith("pp") or
                unit.startswith("percentage") else "x" if unit in ("x", "×") else "plain")
        val = _f(raw)
        if m.group("sign") in ("-", "−"):
            val = -val
        dec = _dec(raw)
        if kind == "plain":
            ctx = s[max(0, m.start() - 14):m.start()]
            if STRUCT_PREV.search(ctx):
                continue
            if dec == 0 and 1900 <= val <= 2100:
                continue
            after = re.match(r"\s+([A-Za-z]+)", s[m.end():])
            noun = after.group(1).lower() if after else ""
            if dec == 0:
                big = abs(val) >= 10
                if not big and noun not in COUNT_NOUNS:
                    continue
        out.append(Num(val, m.group(0).strip(), kind, dec))
    return out


def tokens_of(text):
    ws = re.findall(r"[A-Za-z][A-Za-z]{2,}", text.lower())
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in ws} - STOP


def extract(files):
    """files: list of (rel_path, abs_path, text)."""
    claims = []
    for rel, _abs, text in files:
        for line, sent in sentences(text, rel):
            nums = extract_numbers(sent)
            if nums:
                claims.append(Claim(rel, line, sent, nums, tokens_of(sent)))
    return claims
