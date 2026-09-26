"""Does the cited source say what the sentence says it says? (Layer A: no LLM.)

`citations.py` checks that a citation RESOLVES. This checks a narrower and
harder thing: when a sentence both cites a source and says something specific
about it, is that specific thing there?

It does this only with checks that can be wrong in one direction, on purpose:

    FLAGGED       something the sentence asserts is missing from, or contradicted
                  by, the source, and we read enough of the source to say so.
    UNVERIFIABLE  everything else. Which is most sentences.

It never says VERIFIED. A name, word or number being present in a paper proves
nothing about whether the paper supports the claim; that is the keyword-match
trap, and only a reader (or a reasoning layer with a verifiable quote) can
escape it. So the asymmetry is deliberate: absence is evidence, presence is not.

What it checks, per sentence that cites a source:

    names     'Jin and Candes [cite]': are those people among the source's authors?
    year      'BenchPress [cite] is from 2016': does the record say 2016?
    terms     a proper noun, acronym or compound the sentence uses ('Mondrian')
              that appears nowhere in the source's full text
    numbers   a specific figure the sentence attributes to the source that is
              not in its text
    negation  'X never mentions Y', when the text does mention Y

Absence-based flags (terms, numbers, negation) are only made from full text that
passed its own self-check (fulltext.py) and only when EVERY source cited by the
sentence was read that way; a source we could not read might hold the word.
"""
from __future__ import annotations
import os, re, json, time, unicodedata, urllib.request, urllib.error
from dataclasses import dataclass, field

from . import fulltext as ft
from . import citations as cit
from .claims import extract_numbers
from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

# ------------------------------------------------------------------ parsing
CITE_TEX = re.compile(r"\\(cite[a-zA-Z]*)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
DOI_URL = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s)\"<>]+)")
ABBR = ("et al.", "e.g.", "i.e.", "vs.", "cf.", "Fig.", "Sec.", "Eq.", "No.", "approx.", "resp.", "etc.")

FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us|i|my)\b", re.I)
CLAIM_VERBS = re.compile(
    r"\b(?:show|shows|showed|shown|report|reports|reported|find|finds|found|prove|proves|proved|"
    r"demonstrate|demonstrates|demonstrated|introduce|introduces|introduced|propose|proposes|"
    r"proposed|establish|establishes|established|derive|derives|derived|observe|observes|"
    r"observed|argue|argues|argued|note|notes|noted|study|studies|studied|"
    r"apply|applies|applied|build|builds|built|develop|develops|developed)\b", re.I)
# 'BenchPress [cite] compiles ...': a capitalized subject, then the citation, then a verb.
# The subject IS the source, so what follows is a claim about it.
SUBJECT_CITE = re.compile(r"^(?:[A-Z][\w'`\-]*(?:\s+(?:and|&|et\s+al\.?)(?:\s+|\b))?){1,3}\s*\u27e6C:[^\u27e7]*\u27e7\s+[a-z]+")
SOURCE_REF = re.compile(r"\b(?:their|its|the\s+(?:paper|authors|study|article|work)|they)\b", re.I)
NEG_FRAME = re.compile(
    r"\b(?:do(?:es)?\s+not|never|nowhere|no\s+(?:mention|discussion))\b[^.;]{0,25}?"
    r"\b(?:appear|appears|mention|mentions|mentioned|address|addresses|include|includes|"
    r"contain|contains|discuss|discusses|consider|considers|cover|covers|use|uses|"
    r"report|reports|evaluate|evaluates)\b", re.I)

STOP_CAPS = frozenset("""Section Sections Figure Figures Table Tables Appendix Theorem Lemma Proposition Corollary
Definition Remark Algorithm Equation Chapter Part January February March April May June July August September October
November December Monday Tuesday Wednesday Thursday Friday Saturday Sunday English Python Github GitHub Hugging Face
Google Microsoft Meta Adobe NeurIPS ICML ICLR arXiv ArXiv CoRR Journal Proceedings Conference University Research""".split())
STOP_ACRONYM = frozenset("""LLM LLMS AI ML CI MAE MSE GPU CPU API URL PDF HTML CSV JSON TPU FLOP FLOPS ONLY OOD IID
NLP SOTA PP LOFO OK USA EU UK""".split())
GENERIC = frozenset("""the and for with from that this these those their there where which while when than then
also into over under between within without about such other some many most more each both very only just even
still yet can may will would should could does did has have had are was were been being not but nor per via""".split())


def _clean_tex(raw):
    """Readable text from one TeX sentence, with citations turned into markers."""
    t = re.sub(r"(?<!\\)%.*", "", raw)
    t = CITE_TEX.sub(lambda m: f" \u27e6C:{m.group(1)}:{m.group(2).replace(' ', '')}\u27e7 ", t)
    t = re.sub(r"\\(?:sub)*(?:section|paragraph)\*?\{[^{}]*\}", " ", t)
    t = re.sub(r"\\(?:begin|end)\{[^{}]*\}|\\item\b", " ", t)
    t = re.sub(r"\$[^$]*\$|\\\([^)]*\\\)", " \u27e8M\u27e9 ", t)
    # accents: \'c \"o \`e {\'c} -> c o e c
    t = re.sub(r"\\[`'^\"~=.]\s*\{?(\\?[A-Za-z])\}?", lambda m: m.group(1).lstrip("\\"), t)
    t = t.replace("\\S", "Sec ").replace("~", " ").replace("{,}", ",").replace("\\%", "%")
    t = re.sub(r"\\(?:emph|textit|textbf|texttt|textsc|text|mbox)\{([^{}]*)\}", r"\1", t)
    for _ in range(3):
        t = re.sub(r"\\[A-Za-z]+\*?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[A-Za-z]+\*?", " ", t)
    t = t.replace("{", "").replace("}", "").replace("``", "\"").replace("''", "\"")
    t = re.sub(r"---|--", " - ", t)
    return re.sub(r"\s+", " ", t).strip()


def _clean_md(raw):
    """Markdown sentence -> readable text with a marker for each arXiv/DOI link."""
    idents = []

    def link(m):
        text, url = m.group(1), m.group(2)
        a, d = ARXIV_URL.search(url), DOI_URL.search(url)
        ident = ("arxiv:" + a.group(1)) if a else ("doi:" + d.group(1).rstrip(".,;)")) if d else None
        if not ident:
            return text
        idents.append(ident)
        keep = "" if re.match(r"(?i)\s*(?:arxiv\s*:|doi\s*:|10\.|\d{4}\.)", text) else text
        return f" {keep} \u27e6C:md:{ident}\u27e7 "
    t = MD_LINK.sub(link, raw)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"[*_`]{1,3}", "", t)
    return re.sub(r"\s+", " ", t).strip(), idents


def _split_sentences(text):
    """-> [(line, raw_sentence)] keeping line numbers."""
    out, pos = [], 0
    prot = text
    for a in ABBR:
        prot = prot.replace(a, a.replace(".", "\u2024"))
    prot = re.sub(r"(\\(?:sub)*(?:section|paragraph)\*?\{[^{}]*\})", lambda m: m.group(1) + "\n\n", prot)
    for para in re.split(r"\n\s*\n", prot):
        at = prot.find(para, pos)
        pos = at + len(para) if at >= 0 else pos
        p2 = 0
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\\(\[\"'$])", para):
            k = para.find(s, p2)
            p2 = k + len(s) if k >= 0 else p2
            s = s.replace("\u2024", ".")
            if s.strip():
                out.append((prot.count("\n", 0, max(at, 0) + max(k, 0)) + 1, s))
    return out


# ------------------------------------------------------------------- pairs
@dataclass
class Pair:
    file: str
    line: int
    text: str                     # cleaned sentence, markers removed for display
    idents: list                  # 'arxiv:...' | 'doi:...' | None per cited item
    keys: list
    textual: bool                 # \citet: the author names come from the bibliography
    named: list                   # surnames the prose attributes the source to
    attributive: bool
    marked: str = ""              # cleaned sentence with citation markers, for term/number analysis
    bib_authors: list = field(default_factory=list)
    context: str = ""             # the sentence before, for 'Their matrix ...' style references


def bib_identifier(entry):
    f = entry["fields"]
    eid = f.get("eprint", "")
    if re.fullmatch(r"\d{4}\.\d{4,5}", eid):
        return "arxiv:" + eid
    m = cit.ARXIV_RX.search(f.get("journal", "") + " " + f.get("url", "") + " " + f.get("note", ""))
    if m:
        return "arxiv:" + m.group(1)
    if f.get("doi"):
        return "doi:" + f["doi"].strip()
    u = f.get("url") or (cit.URL_RX.search(f.get("note", "")).group(0) if cit.URL_RX.search(f.get("note", "")) else "")
    if u.startswith("http"):
        return "url:" + u.rstrip(".,;)")
    return None


_NAME = r"[A-Z][A-Za-z'`\-]{2,}"
_NAMED = re.compile(
    rf"((?:{_NAME}(?:\s*,\s*|\s+and\s+|\s*&\s*))*{_NAME})(\s+et\s+al\.?)?\s*(?:'s\s+)?(?:\(\s*)?\u27e6C:")


def _named_authors(marked):
    """Surnames the sentence puts directly in front of a citation marker.

    Only 'X et al.' or a list of two or more names counts. A single capitalized
    word before a citation ('BenchPress [cite]') is as likely a method or system
    name as a person, and calling it an author would flag every such sentence."""
    out = []
    for m in _NAMED.finditer(marked):
        names = [n.strip("'`- ") for n in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", m.group(1))]
        names = [n for n in names if n and n[0].isupper() and n not in STOP_CAPS]
        if m.group(2) or len(names) >= 2:
            out += names
    return out


def find_pairs(text_files, bib_files):
    bib = {}
    for rel, _p, text in bib_files:
        if rel.lower().endswith(".bbl"):
            continue
        for e in cit.parse_bib(text):
            bib.setdefault(e["key"], e)
    pairs = []
    for rel, _p, text in text_files:
        low = rel.lower()
        is_tex = low.endswith(".tex")
        is_md = low.endswith((".md", ".markdown", ".txt", ".rst"))
        if not (is_tex or is_md):
            continue
        prev = ""
        for line, raw in _split_sentences(text):
            before, prev = prev, (_clean_tex(raw) if is_tex else _clean_md(raw)[0])
            before = re.sub(r"\u27e6C:[^\u27e7]*\u27e7|\u27e8M\u27e9", "", before).strip()
            if is_tex:
                if not CITE_TEX.search(raw):
                    continue
                marked = _clean_tex(raw)
                cites = CITE_TEX.findall(raw)
                keys, textual = [], False
                for kind, ks in cites:
                    for k in ks.split(","):
                        keys.append(k.strip())
                    textual = textual or kind in ("citet", "citealt", "citeauthor", "textcite")
                idents = [bib_identifier(bib[k]) if k in bib else None for k in keys]
                bauth = [cit.surnames(bib[k]["fields"]["author"]) if k in bib and bib[k]["fields"].get("author") else []
                         for k in keys]
            else:
                if not (MD_LINK.search(raw) or cit.ARXIV_RX.search(raw)):
                    continue
                marked, idents = _clean_md(raw)
                for m in cit.ARXIV_RX.finditer(marked):          # bare 'arXiv:2606.01850'
                    if "arxiv:" + m.group(1) not in idents:
                        idents.append("arxiv:" + m.group(1))
                if not idents:
                    continue
                keys, textual, bauth = list(idents), False, [[] for _ in idents]
            named = [] if textual else _named_authors(marked)
            attributive = (bool(named) or textual or bool(CLAIM_VERBS.search(marked))
                           or bool(SOURCE_REF.search(marked)) or bool(SUBJECT_CITE.match(marked.strip())))
            display = re.sub(r"\u27e6C:[^\u27e7]*\u27e7", "", marked)
            display = re.sub(r"\s+", " ", display).strip()
            pairs.append(Pair(rel, line, display, idents, keys, textual, named, attributive, marked,
                              [a for b in bauth for a in b], before))
    return pairs


# ------------------------------------------------------------ term analysis
def _key(name):
    """Letters only, accents folded, lowercase: G'Sell, G\u2019Sell and GSell are one name."""
    return re.sub(r"[^a-z]", "", ft.normalize(name))


def _fold(s):
    s = re.sub(r"[`'\u2019]s\b", "", s)
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def candidate_terms(marked, named, authors_known):
    """The words a sentence uses that would be surprising to find missing from a
    source that really discusses the thing: proper nouns, acronyms, compounds.
    Ordinary lowercase words are never candidates, on purpose.
    """
    text = re.sub(r"\u27e6C:[^\u27e7]*\u27e7|\u27e8M\u27e9", " ", marked)
    text = _fold(text)
    skip = {_key(n) for n in named} | {_key(a) for a in authors_known}
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9]|[A-Za-z0-9]", text)
    out, seen = [], set()
    for i, w in enumerate(words):
        w = w.strip(".-")
        if not w or _key(w) in skip:
            continue
        first = i == 0
        kind = None
        if "-" in w and re.search(r"[A-Za-z]{3,}", w):
            kind = "compound"
        elif re.search(r"[A-Za-z]\d|\d[A-Za-z]", w) and re.search(r"[A-Za-z]{2,}", w):
            kind = "alnum"
        elif re.fullmatch(r"[A-Z]{3,}s?", w) and w.rstrip("s") not in STOP_ACRONYM:
            kind = "acronym"
        elif re.fullmatch(r"[A-Z][a-z]+(?:[A-Z][a-z]*)+", w):
            kind = "camel"
        elif re.fullmatch(r"[A-Z][a-z]{3,}", w) and not first and w not in STOP_CAPS \
                and w.lower() not in GENERIC:
            kind = "proper"
        if kind and w.lower() not in seen:
            seen.add(w.lower())
            out.append((w, kind))
    return out


def _term_present(term, kind, idx):
    """Generous on purpose: only what fails every reading is called absent."""
    t = term.lower()
    if kind == "compound":
        parts = [p for p in re.split(r"-", t) if re.fullmatch(r"[a-z]{4,}", p)]
        if not parts:
            return True                              # nothing word-like to look for
        return all(idx.maybe_present(p) for p in parts) or idx.maybe_present(t)
    if kind == "alnum":
        # '848GB' is '848 GB' in a PDF; 'W4A16' is 'W4A16' or 'W4 A16'. Present if every run of
        # letters and digits is present as a word of its own.
        parts = re.findall(r"[a-z]+|\d+", t)
        return idx.maybe_present(t) or (len(parts) > 1 and all(idx._count_norm(p) for p in parts))
    forms = {t, t.rstrip("s"), t + "s"}
    return any(idx.maybe_present(f) for f in forms)


# ---------------------------------------------------------------- checks
def _snip(s, n=140):
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


# What comes after these words is something the citing authors are setting the source
# AGAINST ('a closer domain than BenchPress'), not something they say the source contains.
_CONTRAST = re.compile(
    r"\b(?:than|unlike|versus|vs\.?|compared\s+(?:to|with)|relative\s+to|rather\s+than|instead\s+of|"
    r"in\s+contrast\s+(?:to|with)|as\s+opposed\s+to|whereas|while|but\s+not|except)\b", re.I)


def _claim_text(marked):
    """The part of the sentence that is asserted about the cited source."""
    m = _CONTRAST.search(marked)
    return marked[:m.start()] if m else marked


def _subject_names(pairs):
    """name -> identifiers it labels, for words written directly before a citation
    marker ('BenchPress [cite]'). A name that labels a DIFFERENT source than the one
    being checked is a reference to that other work, not a term to look for here."""
    out = {}
    for p in pairs:
        for m in re.finditer(r"([A-Z][A-Za-z0-9'`\-]{3,})\s*(?:'s\s+)?\u27e6C:", p.marked):
            out.setdefault(m.group(1).lower(), set()).update(i for i in p.idents if i)
    return out


def _tier_note(sources):
    return ", ".join(
        f"{s.ident.split(':', 1)[1]}={s.tier}"
        + ("" if s.tier not in ("html", "pdf") or s.trusted else " (failed self-check)")
        for s in sources)


def _numstr(v):
    v = float(v)
    return str(int(v)) if v == int(v) else str(v).rstrip("0").rstrip(".")


_MARK = "\u27e6C:"
# A year only dates the SOURCE when the sentence says so in one of these shapes.
# 'Since 2020, methods like GPTQ [cite] ...' does not, and must not be flagged.
_YEAR_FRAMES = (
    r"(?:is|was|are|were)\s+(?:a\s+|an\s+|the\s+)?(?:\w+\s+){0,2}?(?:from|of|published\s+in|dated|dating\s+from|released\s+in)\s+(?P<y>{Y})",
    r"(?:the|a|an)\s+(?P<y2>{Y})\s+(?:paper|work|study|article|preprint|release|report)",
    r"\(\s*(?P<y3>{Y})\s*\)",
)
_Y = r"(?:19[5-9]\d|20[0-2]\d)"


def _dated_year(marked):
    """The year the sentence gives for the cited source, or None."""
    if len(re.findall(_Y, re.sub(r"\u27e6C:[^\u27e7]*\u27e7", " ", marked))) != 1:
        return None
    for pat in _YEAR_FRAMES:
        m = re.search(pat.replace("{Y}", _Y), marked)
        if m:
            y = next(v for k, v in m.groupdict().items() if v)
            at = marked.find(y)
            marks = [k.start() for k in re.finditer(re.escape(_MARK), marked)]
            if marks and min(abs(at - k) for k in marks) <= 80:
                return int(y)
    return None


def _negation_stems(marked):
    """Content words of what the sentence says the source does not contain.
    Names (capitalized words) are skipped: they refer to the source itself."""
    m = NEG_FRAME.search(marked)
    if not m:
        return []

    def content(part, allow_initial):
        ws = re.findall(r"[A-Za-z]{6,}", _fold(part))
        out = []
        for k, w in enumerate(ws):
            if w[0].isupper() and not (allow_initial and k == 0 and part.strip().startswith(w)):
                continue
            if w.lower() not in GENERIC:
                out.append(w.lower())
        return out
    after = re.sub(r"\u27e6C:[^\u27e7]*\u27e7", " ", marked[m.end():])
    before = re.sub(r"\u27e6C:[^\u27e7]*\u27e7", " ", marked[:m.start()])
    words = content(after, False) or content(before, True)
    return [w[:max(5, len(w) - 2)] for w in words[:2]]


def assess(pair, sources, attributed=frozenset(), names=None):
    """-> (status, message, evidence). `sources` has one entry per cited item
    (None where the item has no identifier)."""
    ev = f"\u201c{_snip(pair.text)}\u201d"
    real = [s for s in sources if s is not None]
    flags = []

    # -- names the prose gives for the source
    if pair.named and real and all(s.authors for s in real):
        have = {_key(a) for s in real for a in s.authors}
        missing = [n for n in pair.named if _key(n) not in have]
        if missing:
            flags.append(
                f"the sentence names {', '.join(missing)}, who {'is' if len(missing) == 1 else 'are'} "
                f"not among the authors on the record ("
                + "; ".join(s.ident.split(':', 1)[1] + ": " + ", ".join(s.authors[:6]) for s in real) + ")")
    # -- the bibliography's own author list for the source (the citation check reports
    # the same fact per entry; here it is tied to the sentence that relies on it)
    if len(real) == 1 and pair.bib_authors and real[0].authors:
        want = [_key(a) for a in pair.bib_authors]
        have = [_key(a) for a in real[0].authors]
        if want != have[:len(want)] and not (len(want) < len(have) and want == have[:len(want)]):
            flags.append("the bibliography lists authors " + ", ".join(pair.bib_authors[:6])
                         + " but the record lists " + ", ".join(real[0].authors[:6]))
    # -- the date the prose gives for the source
    if len(real) == 1 and real[0].year and not FIRST_PERSON.search(pair.marked):
        y = _dated_year(pair.marked)     # the frame itself ('is from 2016') makes it about the source
        if y and abs(y - real[0].year) >= 2:
            flags.append(f"the sentence dates the source to {y}; the record says {real[0].year}")

    # -- absence-based checks: every cited source must have been read in full and self-checked
    all_trusted = bool(real) and len(real) == len(sources) and all(s.trusted for s in real)
    if all_trusted:
        authors_known = [a for s in real for a in s.authors]
        if NEG_FRAME.search(pair.marked):
            # 'X never mentions Y': a claim of ABSENCE, so it is the presence of Y that contradicts it.
            if len(real) == 1:
                stems = _negation_stems(pair.marked)
                counts = {st: sum(c for t_, c in real[0].index.counts.items() if t_.startswith(st))
                          for st in stems}
                if stems and all(n >= 2 for n in counts.values()):
                    flags.append("the sentence says the source does not use this, but its text does: "
                                 + ", ".join(f"'{k}*' x{v}" for k, v in counts.items())
                                 + " (check that it is the same thing)")
        else:
            claim = _claim_text(pair.marked)
            terms = candidate_terms(claim, pair.named, authors_known)
            mine = {i for i in pair.idents if i}
            terms = [(w, k) for w, k in terms
                     if not ((names or {}).get(w.lower()) and not ((names or {})[w.lower()] & mine))]
            absent = [(w, k) for w, k in terms if not any(_term_present(w, k, s.index) for s in real)]
            if absent and not pair.attributive:
                # A background citation: authors coin their own words. A word used only inside
                # citing sentences is being attributed; one used freely elsewhere is theirs.
                absent = [(w, k) for w, k in absent if w.lower() in attributed]
            if absent:
                flags.append("not found in the text we read of "
                             + ", ".join(s.ident.split(":", 1)[1] for s in real) + ": "
                             + ", ".join(f"'{w}'" for w, _ in absent[:5]))
            if pair.attributive and not FIRST_PERSON.search(pair.marked):
                miss = []
                for n in extract_numbers(re.sub(r"\u27e6C:[^\u27e7]*\u27e7", " ", claim)):
                    if n.kind == "plain" and n.decimals == 0 and abs(n.value) < 10:
                        continue
                    forms = [_numstr(abs(n.value))] + ([_numstr(n.den)] if n.den else [])
                    if not all(any(f in s.index.nums for s in real) for f in forms):
                        miss.append(n.raw)
                if miss:
                    flags.append("figure(s) not found in the text we read: " + ", ".join(miss[:4])
                                 + " (a paraphrase such as 'half a million' would look like this too)")
    if flags:
        ids = ", ".join(s.ident.split(":", 1)[1] for s in real)
        return FLAGGED, f"cited source ({ids}): " + "; ".join(flags), ev
    if len(real) < len(sources):
        why = "a cited item has no arXiv id, DOI or URL, so it could not be looked up"
    elif not all_trusted:
        why = "the full text could not be read and self-checked (" + _tier_note(real) + ")"
    else:
        why = "nothing checkable that could be shown absent; presence would not have proved support"
    return UNVERIFIABLE, f"support not checked: {why}", ev


def _attributed_terms(pairs, text_files):
    """Per file: candidate terms that occur ONLY inside sentences that cite
    something. Such a word is being attributed to a source; a word the author
    also uses elsewhere is their own vocabulary and not the source's to contain."""
    by_file = {}
    for p in pairs:
        by_file.setdefault(p.file, []).append(p)
    out = {}
    for rel, _p, text in text_files:
        prs = by_file.get(rel)
        if not prs:
            continue
        body = _fold(_clean_tex(text) if rel.lower().endswith(".tex") else text).lower()
        cited = " ".join(_fold(p.marked).lower() for p in prs)
        terms = set()
        for p in prs:
            for w, _k in candidate_terms(p.marked, p.named, p.bib_authors):
                if len(re.findall(re.escape(w.lower()), body)) <= len(re.findall(re.escape(w.lower()), cited)):
                    terms.add(w.lower())
        out[rel] = terms
    return out


# ------------------------------------------------------------- providers
def _get_bytes(url, timeout=60, max_bytes=15_000_000):
    """-> (status, bytes, truncated). Unlike citations.default_fetch this never
    silently cuts a body short: a cut paper would make later sections' words
    look absent, so truncation is reported and makes the source untrusted."""
    if "arxiv.org" in url:                     # one polite clock for API, HTML and PDF alike
        wait = cit.ARXIV_MIN_INTERVAL - (time.time() - cit._last_arxiv_call)
        if wait > 0:
            time.sleep(wait)
        cit._last_arxiv_call = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": cit.UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=cit.tls_context()) as r:
            data = r.read(max_bytes + 1)
            cit._last_arxiv_call = time.time() if "arxiv.org" in url else cit._last_arxiv_call
            return r.status, data[:max_bytes], len(data) > max_bytes
    except urllib.error.HTTPError as e:
        return e.code, b"", False
    except Exception:
        return 0, b"", False


def parse_arxiv_record(xml):
    e = re.search(r"<entry>(.*?)</entry>", xml, re.S)
    if not e:
        return None
    e = e.group(1)
    g = lambda tag: re.sub(r"\s+", " ", (re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", e, re.S) or [None, ""])[1]).strip()
    au = re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)
    pub = g("published")
    return {"title": g("title"), "abstract": g("summary"),
            "authors": [ft.normalize(a).split()[-1] for a in au if ft.normalize(a).split()],
            "year": int(pub[:4]) if pub[:4].isdigit() else 0}


def parse_arxiv_abs(html):
    """Metadata from an arxiv.org/abs page, for when the API is refusing us (it
    throttles bursts with 406/429 while the abstract pages keep answering)."""
    def meta(name):
        return [re.sub(r"\s+", " ", m).strip() for m in re.findall(
            rf'<meta name="{name}" content="([^"]*)"', html)]
    t = meta("citation_title")
    if not t:
        return None
    import html as _h
    au = [_h.unescape(a).split(",")[0].strip() for a in meta("citation_author")]
    d = meta("citation_date")
    ab = re.search(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>', html, re.S)
    abstract = re.sub(r"^\s*Abstract:\s*", "", _h.unescape(re.sub(r"<[^>]+>", " ", ab.group(1)))).strip() if ab else ""
    return {"title": _h.unescape(t[0]), "abstract": re.sub(r"\s+", " ", abstract),
            "authors": [ft.normalize(a).split()[-1] for a in au if ft.normalize(a).split()],
            "year": int(d[0][:4]) if d and d[0][:4].isdigit() else 0}


class NetworkProvider:
    """Fetches, self-checks and caches sources. `pdf` False skips PDFs."""

    def __init__(self, fetch=cit.default_fetch, get_bytes=_get_bytes, pdf=True, sleep=time.sleep):
        self.fetch, self.get_bytes, self.pdf, self.sleep = fetch, get_bytes, pdf, sleep
        self.memo = {}

    def __call__(self, ident):
        if ident in self.memo:
            return self.memo[ident]
        try:
            src = self._load(ident)
        except Exception as e:                       # a source must never crash the audit
            src = ft.Source(ident, note=f"lookup failed: {type(e).__name__}")
        self.memo[ident] = src
        return src

    def _load(self, ident):
        kind, _, val = ident.partition(":")
        if kind == "arxiv":
            st, body = self.fetch(cit.ARXIV_API + val)
            # arXiv answers a burst of requests with 406/429/503 and nothing is wrong
            # with the id; wait and ask again rather than reporting a real paper unreadable.
            for pause in (8, 20):
                if st in cit.BLOCKED or st == 0:
                    self.sleep(pause)
                    st, body = self.fetch(cit.ARXIV_API + val)
            rec = parse_arxiv_record(body) if st == 200 else None
            if not rec:                                   # the API is throttled; the abstract page is not
                sa, page, _t = self.get_bytes(f"https://arxiv.org/abs/{val}")
                rec = parse_arxiv_abs(page.decode("utf-8", "replace")) if sa == 200 else None
            if not rec:
                return ft.Source(ident, note=f"arXiv record unavailable (API status {st})")
            html = pdf = None
            note = ""
            s1, data, trunc = self.get_bytes(f"https://arxiv.org/html/{val}")
            if s1 == 200 and data and not trunc:
                html = data.decode("utf-8", "replace")
            elif s1 == 200 and trunc:
                note = "HTML page was larger than the download limit, so it was not used"
            else:
                note = f"no HTML version (status {s1})"
            if html is None and self.pdf:
                s2, data, trunc = self.get_bytes(f"https://arxiv.org/pdf/{val}")
                if s2 == 200 and data and not trunc:
                    pdf = data
                elif s2 == 200 and trunc:
                    note += "; PDF larger than the download limit"
            src = ft.build_source(ident, rec["title"], rec["authors"], rec["year"], rec["abstract"],
                                  html=html, pdf=pdf, note=note)
            if not src.trusted and html is not None and self.pdf:
                s2, data, trunc = self.get_bytes(f"https://arxiv.org/pdf/{val}")
                if s2 == 200 and data and not trunc:
                    alt = ft.build_source(ident, rec["title"], rec["authors"], rec["year"],
                                          rec["abstract"], html=None, pdf=data, note=src.note)
                    if alt.trusted or alt.tier == "pdf":
                        src = alt
            return src
        if kind == "doi":
            import urllib.parse
            st, body = self.fetch("https://api.crossref.org/works/" + urllib.parse.quote(val, safe="/"))
            if st != 200:
                return ft.Source(ident, note=f"Crossref unavailable (status {st})")
            msg = json.loads(body)["message"]
            year = ((msg.get("issued") or {}).get("date-parts") or [[0]])[0][0] or 0
            abst = re.sub(r"<[^>]+>", " ", msg.get("abstract", "") or "")
            return ft.build_source(ident, (msg.get("title") or [""])[0],
                                   [ft.normalize(a.get("family", "")).split()[-1]
                                    for a in msg.get("author", []) if a.get("family")],
                                   int(year), re.sub(r"\s+", " ", abst).strip(),
                                   note="full text of DOI-only sources is not fetched")
        if kind == "url":
            st, data, trunc = self.get_bytes(val)
            if st != 200 or not data:
                return ft.Source(ident, note=f"page unavailable (status {st})")
            if trunc:
                return ft.Source(ident, note="page larger than the download limit, so it was not used")
            return ft.build_web_source(ident, data.decode("utf-8", "replace"))
        return ft.Source(ident, note="not an arXiv id, DOI or web page")


# ------------------------------------------------------------------ check
def check(text_files, bib_files, provider=None, offline=False, max_sources=60, judge=None):
    pairs = find_pairs(text_files, bib_files)
    if not pairs:
        return []
    out = []
    if offline:
        return [Finding("support", UNVERIFIABLE, p.file, p.line, "support not checked (offline)", f"\u201c{_snip(p.text)}\u201d")
                for p in pairs]
    provider = provider or NetworkProvider()
    attributed = _attributed_terms(pairs, text_files)
    names = _subject_names(pairs)
    ids = []
    for p in pairs:
        for i in p.idents:
            if i and i not in ids:
                ids.append(i)
    over = set(ids[max_sources:])
    for p in pairs:
        srcs = []
        for i in p.idents:
            if i is None:
                srcs.append(None)
            elif i in over:
                srcs.append(ft.Source(i, note="beyond --max-urls"))
            else:
                srcs.append(provider(i))
        status, msg, ev = assess(p, srcs, attributed.get(p.file, frozenset()), names)
        extra = {"sources": {s.ident: {"tier": s.tier, "trusted": s.trusted, "note": s.note}
                             for s in srcs if s is not None}}
        if judge is not None and status == UNVERIFIABLE:
            status, msg, ev, more = _judged(judge, p, srcs, status, msg, ev)
            extra.update(more)
        out.append(Finding("support", status, p.file, p.line, msg, ev, extra))
    return out


def _judged(judge, pair, srcs, status, msg, ev):
    """Layer B for one sentence whose Layer A answer was UNVERIFIABLE. Returns
    (status, message, evidence, extra). Only a single, fully read source is judged;
    everything else keeps its Layer A answer and says why it was not judged."""
    from . import judge as jd
    if len(srcs) != 1 or srcs[0] is None:
        return status, msg + "; not judged (the sentence cites several sources, or none that could be looked up)", ev, {}
    s = srcs[0]
    if not (s.trusted and s.text):
        return status, msg + "; not judged (the source's full text was not available and self-checked)", ev, {}
    if not judge.budget_left():
        return status, msg + f"; not judged (the limit of {judge.max_calls} model calls was reached)", ev, {}
    v = judge.judge(pair, s)
    more = {"verdict": v.verdict, "quote": v.quote, "excerpt": v.excerpt, "reason": v.reason,
            "note": v.note, "model": judge.client.model, "windows": v.windows}
    sid = s.ident.split(":", 1)[1]
    if v.verdict == jd.SUPPORTED:
        return (VERIFIED, f"[EXPERIMENTAL, UNVALIDATED] SUPPORTED by a quoted passage of {sid} (model-judged; a receipt to check, not a proof): {v.reason}",
                f"{ev}  <-  \u201c{v.quote}\u201d", more)
    if v.verdict == jd.CONTRADICTED:
        return (FLAGGED, f"[EXPERIMENTAL, UNVALIDATED] CONTRADICTED by a quoted passage of {sid}: {v.reason}", f"{ev}  vs  \u201c{v.quote}\u201d", more)
    why = v.note or v.reason or "the passages shown neither support nor contradict it"
    return UNVERIFIABLE, f"[EXPERIMENTAL] UNCLEAR \u2014 NEEDS HUMAN REVIEW ({sid}): {why}", ev, more
