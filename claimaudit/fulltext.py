"""Full text of a cited source, and a way to ask it "is this word in there?".

The support check (support.py) asks questions like "does this paper ever use
the word Mondrian?". A wrong "no" here is worse than not asking, so this
module is built around one rule: **an extraction that cannot prove it read the
paper correctly is never allowed to say a word is absent.**

How it proves that. Every source comes with clean metadata (title, abstract)
that does not depend on text extraction. The extracted text must contain that
metadata's words. If a two-column layout, a ligature or a broken font has
mangled the text, the words we know are there go missing and the extraction is
marked untrustworthy (`Source.trusted` is False). Absence checks then refuse to
run. This is the "canary" below.

Sources, best first: arXiv's HTML rendering of the paper (clean, no layout to
misread), then a PDF if the optional `pypdf` package is installed, then just
the abstract. Only the first two can support an absence claim.
"""
from __future__ import annotations
import json, os, re, unicodedata
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

# ---------------------------------------------------------------- normalizing
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
              "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_DASHES = "‐‑‒–—―−­"   # hyphens, dashes, minus, soft hyphen
_DASH_RX = re.compile("[" + _DASHES + "]")
_TOKEN_RX = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_NUM_RX = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_STOP = frozenset("""a an the and or of in on for to with by from as at is are was were be been this that these
those it its we our they their which who what when where how than then there here also not no but if
so such can may might will would should could do does did has have had using use used into over under
between both each other more most some any all only very""".split())


def normalize(text):
    """Lowercase, fold accents, undo ligatures and dash variants. Returns str.

    Canonical (NFD) rather than compatibility (NFKD) decomposition on purpose:
    NFKD turns the math letter l-script into a plain "l", which glues it onto
    the word before it ("every" + l -> "everyl") and hides the word."""
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    # PDF extractors write "Pek\u00a8 oz" and "na\u00a8 \u0131ve": accent, space, letter
    text = re.sub("([\u00a8\u00b4`\u02c6\u02dc\u00b8\u02d9\u02da\u02dd\u02c7\u00af])\\s*", "", text)
    text = text.replace("\u0131", "i")
    text = unicodedata.normalize("NFD", text)
    # combining accents (Mn) and the spacing accents a PDF leaves beside a
    # letter (Sk: the diaeresis in "Pek\u00a8oz") both vanish, so the name matches
    text = "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Sk"))
    text = text.replace("{,}", ",")
    text = _DASH_RX.sub("-", text)
    return text.lower()


def tokens(norm_text):
    return _TOKEN_RX.findall(norm_text)


def dehyphenate(norm_text):
    """Join words a line break split ('mon- drian' -> 'mondrian').

    This cannot tell a broken word from a real compound ('state- of-the-art'),
    so it is only ever used to ADD tokens to the index, never to replace them.
    Extra tokens can only stop a word being called absent, which is the safe
    direction."""
    return re.sub(r"([a-z])-\s+([a-z])", r"\1\2", norm_text)


def numbers(norm_text):
    out = set()
    for m in _NUM_RX.finditer(norm_text):
        s = m.group(0).replace(",", "")
        out.add(s)
        if "." in s:
            out.add(s.rstrip("0").rstrip("."))
    return out


# --------------------------------------------------------------------- index
@dataclass
class TextIndex:
    """What the checks may ask of a source. Deliberately keeps no running text:
    a bag of tokens is enough for "is it there" and cannot be used to
    reconstruct the work, so it is also what the test fixtures store."""
    counts: Counter = field(default_factory=Counter)      # body tokens
    ref_counts: Counter = field(default_factory=Counter)  # tokens seen only in the bibliography
    nums: set = field(default_factory=set)
    frags: set = field(default_factory=set)               # 'bottle' from 'bottle-<column break>'
    squashed: str = ""                                     # letters+digits only, for split-word matches
    n_chars: int = 0

    @classmethod
    def from_text(cls, body, refs=""):
        nb = normalize(body)
        c = Counter(tokens(nb))
        # Everything below only ADDS tokens. Extra tokens can stop a word being
        # called absent; they can never make a present word look missing.
        for t in list(c):
            if "-" in t:                                   # compound counts as its parts
                for p in t.split("-"):
                    c[p] += c[t]
            if re.search(r"[a-z]", t) and re.search(r"\d", t):   # 'total0' is 'total' + '0'
                for p in re.findall(r"[a-z]+|\d+", t):
                    c[p] += c[t]
        for t in tokens(dehyphenate(nb)):                  # word split by a line break
            if t not in c:
                c[t] = 1
        camel = re.sub(r"([a-z])([A-Z])", r"\1 \2", body)  # 'becauseP' -> 'because P'
        for t in tokens(normalize(camel)):
            if t not in c:
                c[t] = 1
        nr = normalize(refs)
        rc = Counter(tokens(nr))
        # A word split at a column or page break has its second half far away
        # ("bottle-" ... "neck" on the next column), where no join can find it.
        # Remember the first halves so a word starting with one is not called absent.
        frags = {m.group(1) for m in re.finditer(r"([a-z]{4,})-[ \t]*\n", nb)}
        return cls(counts=c, ref_counts=rc, nums=numbers(nb), frags=frags,
                   squashed=re.sub(r"[^a-z0-9]", "", nb), n_chars=len(body))

    def count(self, term):
        """Exact occurrences of a token in the body. Multi-word terms need all words."""
        return self._count_norm(normalize(term))

    def _count_norm(self, t):
        parts = tokens(t)
        if not parts:
            return 0
        if len(parts) == 1:
            return self.counts.get(parts[0], 0)
        return min(self.counts.get(p, 0) for p in parts)

    def maybe_present(self, term):
        """Generous presence test, used before calling something ABSENT.
        Also looks in the bibliography and in the letters-only text, so a word
        split by layout ('V ovk', 'Mon-drian') is still found."""
        t = normalize(term)
        if self._count_norm(t) or any(self.ref_counts.get(p, 0) for p in tokens(t)):
            return True
        # A PDF font whose ligature glyph did not map to text loses "fi", "fl", "ff"
        # outright ("coefficient" -> "coecient"). Look for the word as that would leave it.
        for lig in ("ffi", "ffl", "fi", "fl", "ff"):
            if lig in t and self._count_norm(t.replace(lig, "")):
                return True
        sq = re.sub(r"[^a-z0-9]", "", t)
        if any(len(sq) > len(f) and sq.startswith(f) for f in self.frags):
            return True
        return len(sq) >= 6 and sq in self.squashed

    def to_json(self):
        """What a test fixture keeps: token counts, never the text."""
        return {"counts": dict(self.counts), "ref_counts": dict(self.ref_counts),
                "nums": sorted(self.nums), "frags": sorted(self.frags), "n_chars": self.n_chars}

    @classmethod
    def from_json(cls, d):
        return cls(counts=Counter(d["counts"]), ref_counts=Counter(d["ref_counts"]),
                   nums=set(d["nums"]), frags=set(d.get("frags", ())), n_chars=d.get("n_chars", 0))


# ------------------------------------------------------------- html -> text
class _Html(HTMLParser):
    SKIP = {"script", "style", "head", "nav", "header", "footer", "button", "noscript", "svg"}

    def __init__(self, keep_math=True, content_only=True):
        super().__init__(convert_charrefs=True)
        self.keep_math = keep_math
        self.content_only = content_only
        self.seen_content = False
        self._content_depth = None
        self.body, self.refs = [], []
        self._skip = 0
        self._math = 0
        self._refs_depth = None
        self._stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        self._stack.append((tag, "ltx_bibliography" in cls))
        if "ltx_page_content" in cls and self._content_depth is None:
            self._content_depth = len(self._stack)
            self.seen_content = True
        if tag in self.SKIP:
            self._skip += 1
        if "ltx_bibliography" in cls and self._refs_depth is None:
            self._refs_depth = len(self._stack)
        if tag == "math":
            alt = a.get("alttext")
            inside = self._content_depth is not None or not self.content_only
            # Formulas are dropped from plain text, except a bare number: "90\\%" typeset in math
            # is still a figure the paper states, and a reader that cannot see it cannot check it.
            simple = bool(alt) and re.fullmatch(r"[\d.,\s\\%+\-\u2212/]+", alt) is not None
            if alt and inside and (self.keep_math or simple):
                (self.refs if self._refs_depth else self.body).append(" " + alt.replace("\\%", "%") + " ")
            self._math += 1
        if tag in ("p", "br", "div", "li", "tr", "section", "h1", "h2", "h3", "h4", "figcaption", "caption"):
            (self.refs if self._refs_depth else self.body).append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "math" and self._math:
            self._math -= 1
        while self._stack:
            t, _ = self._stack.pop()
            if self._refs_depth is not None and len(self._stack) < self._refs_depth:
                self._refs_depth = None
            if self._content_depth is not None and len(self._stack) < self._content_depth:
                self._content_depth = None
            if t == tag:
                break

    def handle_data(self, data):
        if self._skip or self._math:
            return
        if self.content_only and self._content_depth is None:
            return
        (self.refs if self._refs_depth else self.body).append(data)


def html_to_text(html, keep_math=True):
    """-> (body_text, refs_text). keep_math=False drops formulas entirely, which
    is what you want when asking "which words did the author write"."""
    p = _Html(keep_math, content_only=True)
    p.feed(html)
    if not p.seen_content:            # not a LaTeXML page: take everything, honestly
        p = _Html(keep_math, content_only=False)
        p.feed(html)
    return "".join(p.body), "".join(p.refs)


# ------------------------------------------------------------------ quality
def quality(index, text_sample, title="", abstract="", pages=None, failed_pages=0, refs_tokens=None,
            tail_ok=None):
    """Judge an extraction by what it must contain. Returns a dict with
    `trusted` and the numbers behind it, so a report can say why.

    The canary counts a known word as found only if it is a real token of the
    text. It deliberately does NOT use the generous letters-only search that
    rescues a single word: letter-spaced text ("c o n f o r m a l") would pass
    that, and detecting it is the canary's job."""
    n_words = sum(index.counts.values())
    toks = tokens(normalize(text_sample)) if text_sample else []
    single = sum(1 for t in toks if len(t) == 1 and t.isalpha() and t not in ("a", "i"))
    frac_single = single / max(1, len(toks))
    frac_repl = (text_sample.count("\ufffd") / max(1, len(text_sample))) if text_sample else 0.0

    def hit_rate(words):
        words = sorted(set(words))
        if not words:
            return None, 0
        return sum(1 for w in words if index._count_norm(w)) / len(words), len(words)

    tw = [w for w in tokens(normalize(title)) if len(w) >= 4 and w not in _STOP]
    aw = [w for w in tokens(normalize(abstract)) if len(w) >= 6 and w not in _STOP and "-" not in w]
    t_rate, t_n = hit_rate(tw)
    a_rate, a_n = hit_rate(aw)
    reasons = []
    if n_words < 1500:
        reasons.append(f"only {n_words} words extracted")
    if frac_repl > 0.005:
        reasons.append(f"{100*frac_repl:.1f}% replacement characters")
    # Math-heavy PDFs legitimately run to about 10% single-letter tokens (measured); real
    # letter-spacing is far above that, and also fails the canary below.
    if frac_single > 0.20:
        reasons.append(f"{100*frac_single:.0f}% of words are single letters (letter-spaced text)")
    if a_rate is not None and a_n >= 8 and a_rate < 0.95:
        reasons.append(f"only {a_rate:.0%} of the abstract's words were found in the text")
    if t_rate is not None and t_n >= 2 and t_rate < 0.75:
        reasons.append(f"only {t_rate:.0%} of the title's words were found in the text")
    if a_rate is None and t_rate is None:
        reasons.append("no title or abstract to check the extraction against")
    if failed_pages:
        reasons.append(f"{failed_pages} of {pages} pages could not be read")
    if pages and n_words / pages < 100:
        reasons.append(f"only {n_words // pages} words per page on average")
    if refs_tokens is not None and refs_tokens < 50:
        reasons.append("no bibliography was found, so the text may be cut short")
    if tail_ok is False:
        reasons.append("the end of the document was not found, so the text may be cut short")
    return {"trusted": not reasons, "reasons": reasons, "n_words": n_words, "pages": pages,
            "single_letter_frac": round(frac_single, 4), "replacement_frac": round(frac_repl, 5),
            "abstract_hit": None if a_rate is None else round(a_rate, 3), "abstract_n": a_n,
            "title_hit": None if t_rate is None else round(t_rate, 3), "title_n": t_n}


# -------------------------------------------------------------------- source
@dataclass
class Source:
    ident: str                       # 'arxiv:1209.2673' or 'doi:10....'
    title: str = ""
    authors: list = field(default_factory=list)    # surnames, normalized
    year: int = 0
    abstract: str = ""
    tier: str = "none"               # html | pdf | web | abstract | metadata | none
    index: TextIndex | None = None
    text: str = ""                   # plain body text, for choosing passages to show a reader
    qual: dict = field(default_factory=dict)
    note: str = ""                   # why a better tier was not reached

    @property
    def trusted(self):
        """True only when full text was read AND passed its self-check."""
        return self.tier in ("html", "pdf", "web") and bool(self.qual.get("trusted"))


def has_reference_tail(text):
    """True if the text ends the way a paper does: a References heading followed
    by a run of dated entries. The abstract canary only proves the START was read;
    a cut-off text passes it, so the end has to be checked as well."""
    heads = [m for m in re.finditer(r"(?im)^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited)\s*$", text)]
    if not heads:
        return False
    tail = text[heads[-1].end():]
    return len(re.findall(r"\b(?:19|20)\d\d\b", tail)) >= 8


def pdf_to_text(data):
    """Extract text with the optional `pypdf` package.

    -> (text, n_pages, n_failed_pages), or None if pypdf is missing or the file
    cannot be opened. Every page is accounted for: a page that raises is counted,
    not skipped, because a missing page means missing words."""
    try:
        import io
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        r = PdfReader(io.BytesIO(data))
        parts, failed = [], 0
        for pg in r.pages:
            try:
                parts.append(pg.extract_text() or "")
            except Exception:
                failed += 1
        return "\n".join(parts), len(r.pages), failed
    except Exception:
        return None


def build_source(ident, title="", authors=(), year=0, abstract="", html=None, pdf=None, note=""):
    """Turn what was fetched into a Source, best tier first, and self-check it."""
    src = Source(ident, title, list(authors), year, abstract, note=note)
    if html:
        body, refs = html_to_text(html)
        plain, _ = html_to_text(html, keep_math=False)      # formulas would read as single letters
        idx = TextIndex.from_text(body, refs)
        # Completeness: a page cut off mid-download lacks its closing tag. (The reference
        # list itself is no test: some arXiv HTML renders an empty bibliography.)
        closed = bool(re.search(r"</html>\s*$", html, re.I))
        q = quality(idx, plain[:200000], title, abstract, tail_ok=closed)
        if q["trusted"]:
            src.tier, src.index, src.qual, src.text = "html", idx, q, plain
            return src
        src.note = (note + "; " if note else "") + "HTML text failed its self-check: " + "; ".join(q["reasons"])
    if pdf:
        got = pdf_to_text(pdf)
        if got:
            text, pages, failed = got
            idx = TextIndex.from_text(text)
            q = quality(idx, text[:200000], title, abstract, pages=pages, failed_pages=failed,
                        tail_ok=has_reference_tail(text))
            src.tier, src.index, src.qual, src.text = "pdf", idx, q, text
            if not q["trusted"]:
                src.note = (src.note + "; " if src.note else "") + \
                    "PDF text failed its self-check: " + "; ".join(q["reasons"])
            return src
        src.note = (src.note + "; " if src.note else "") + \
            "PDF not read (install `pypdf` to enable, or the file could not be parsed)"
    if abstract:
        src.tier = "abstract"
    elif title or authors:
        src.tier = "metadata"
    return src


def build_web_source(ident, html, title_hint=""):
    """A plain web page (a blog post, a report). Trusted only if it was downloaded
    whole, has a title whose words are in the text, and has a body's worth of words."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    import html as _h
    title = _h.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else title_hint
    body, refs = html_to_text(html)
    idx = TextIndex.from_text(body, refs)
    q = quality(idx, body[:200000], title, "", tail_ok=bool(re.search(r"</html>\s*$", html, re.I)))
    src = Source(ident, title, [], 0, "")
    src.tier, src.index, src.qual, src.text = "web", idx, q, body
    if not q["trusted"]:
        src.note = "web page failed its self-check: " + "; ".join(q["reasons"])
    return src
